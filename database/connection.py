"""MySQL connection configuration for GurmadNet AI.

Credentials are loaded in this order (first wins per key):
  1. Process environment variables (Render / OS / shell)
  2. DATABASE_URL / MYSQL_URL / MYSQL_DSN (mysql://… or mysql+pymysql://…)
  3. Project-root `.env` (local development via python-dotenv)
  4. Optional `database/db_config.env` (legacy local file — not required on Render)

Supported variable names:
  MYSQL_HOST / DB_HOST
  MYSQL_PORT / DB_PORT
  MYSQL_USER / DB_USER
  MYSQL_PASSWORD / DB_PASSWORD
  MYSQL_DATABASE / DB_NAME
  DATABASE_URL / MYSQL_URL / MYSQL_DSN
"""
from __future__ import annotations

import os
from urllib.parse import unquote, urlparse

_CONFIG = None

_ENV_KEY_GROUPS = (
    ("MYSQL_HOST", "DB_HOST"),
    ("MYSQL_PORT", "DB_PORT"),
    ("MYSQL_USER", "DB_USER"),
    ("MYSQL_PASSWORD", "DB_PASSWORD"),
    ("MYSQL_DATABASE", "DB_NAME"),
)

_URL_KEYS = ("DATABASE_URL", "MYSQL_URL", "MYSQL_DSN")


def _on_render(environ=None):
    environ = environ if environ is not None else os.environ
    return (environ.get("RENDER") or "").strip().lower() in ("1", "true", "yes", "on")


def _parse_env_file(path):
    cfg = {}
    if not os.path.exists(path):
        return cfg
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            cfg[key.strip()] = value.strip().strip('"').strip("'")
    return cfg


def _project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _merged_file_config():
    """Optional file-based fallbacks for local development only (ignored when absent)."""
    # On Render the gitignored .env / db_config.env are not deployed — env vars only.
    if _on_render():
        return {}
    root = _project_root()
    base = os.path.dirname(os.path.abspath(__file__))
    merged = {}
    merged.update(_parse_env_file(os.path.join(base, "db_config.env")))
    try:
        from dotenv import dotenv_values

        root_env = dotenv_values(os.path.join(root, ".env")) or {}
        for k, v in root_env.items():
            if v is None:
                continue
            val = str(v).strip()
            if val:
                merged[k] = val
    except ImportError:
        merged.update(_parse_env_file(os.path.join(root, ".env")))
    return merged


def parse_database_url(url):
    """
    Parse mysql://user:pass@host:3306/dbname into a config dict.
    Returns None if url is empty or not a MySQL URL.
    """
    url = (url or "").strip()
    if not url:
        return None
    # SQLAlchemy-style prefix
    if url.startswith("mysql+pymysql://"):
        url = "mysql://" + url[len("mysql+pymysql://") :]
    if url.startswith("mysql+mysqlconnector://"):
        url = "mysql://" + url[len("mysql+mysqlconnector://") :]
    if not (url.startswith("mysql://") or url.startswith("mariadb://")):
        return None
    parsed = urlparse(url)
    if not parsed.hostname:
        return None
    database = (parsed.path or "").lstrip("/")
    if not database:
        return None
    return {
        "host": parsed.hostname,
        "port": int(parsed.port or 3306),
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": database,
    }


def _url_from_environ(environ=None):
    environ = environ if environ is not None else os.environ
    for key in _URL_KEYS:
        raw = (environ.get(key) or "").strip()
        if raw:
            parsed = parse_database_url(raw)
            if parsed:
                return parsed
    return None


def mysql_credentials_present(environ=None, file_cfg=None):
    """True when enough MySQL settings exist to attempt a connection (no file required)."""
    environ = environ if environ is not None else os.environ
    if _url_from_environ(environ):
        return True
    if file_cfg is None:
        file_cfg = _merged_file_config()

    def _has(*keys):
        for key in keys:
            if str(environ.get(key) or "").strip():
                return True
            if str(file_cfg.get(key) or "").strip():
                return True
        return False

    return (
        _has("MYSQL_HOST", "DB_HOST")
        and _has("MYSQL_USER", "DB_USER")
        and _has("MYSQL_DATABASE", "DB_NAME")
    )


def load_config():
    """Load DB settings from environment variables, URL, then optional local files."""
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG.copy()

    file_cfg = _merged_file_config()
    url_cfg = _url_from_environ()

    def _get(*keys, default=""):
        for key in keys:
            if key in os.environ and str(os.environ.get(key) or "").strip() != "":
                return os.environ[key]
            if key in file_cfg and str(file_cfg.get(key) or "").strip() != "":
                return file_cfg[key]
        return default

    if url_cfg:
        host = _get("MYSQL_HOST", "DB_HOST", default="") or url_cfg["host"]
        port = _get("MYSQL_PORT", "DB_PORT", default="") or str(url_cfg["port"])
        user = _get("MYSQL_USER", "DB_USER", default="") or url_cfg["user"]
        password = _get("MYSQL_PASSWORD", "DB_PASSWORD", default="")
        if password == "":
            password = url_cfg["password"]
        database = _get("MYSQL_DATABASE", "DB_NAME", default="") or url_cfg["database"]
    else:
        # Local defaults only — never use localhost silently on Render.
        default_host = "" if _on_render() else "127.0.0.1"
        default_user = "" if _on_render() else "root"
        default_db = "" if _on_render() else "gurmad"
        host = _get("MYSQL_HOST", "DB_HOST", default=default_host)
        port = _get("MYSQL_PORT", "DB_PORT", default="3306")
        user = _get("MYSQL_USER", "DB_USER", default=default_user)
        password = _get("MYSQL_PASSWORD", "DB_PASSWORD", default="")
        database = _get("MYSQL_DATABASE", "DB_NAME", default=default_db)

    if _on_render() and host in ("", "127.0.0.1", "localhost"):
        raise RuntimeError(
            "MYSQL_HOST is missing or points to localhost on Render. "
            "Set MYSQL_HOST to your Render MySQL private service host "
            "(or set DATABASE_URL=mysql://user:pass@host:3306/dbname)."
        )
    if _on_render() and (not user or not database):
        raise RuntimeError(
            "MYSQL_USER and MYSQL_DATABASE are required on Render "
            "(or provide DATABASE_URL)."
        )

    _CONFIG = {
        "host": host,
        "port": int(port or "3306"),
        "user": user,
        "password": password,
        "database": database,
        "charset": "utf8mb4",
        "autocommit": True,
    }
    return _CONFIG.copy()


def config_source_hint():
    """Safe hint for error messages (never includes password)."""
    if any(str(os.environ.get(k) or "").strip() for k in _URL_KEYS):
        return "DATABASE_URL / MYSQL_URL"
    if any(str(os.environ.get(k) or "").strip() for group in _ENV_KEY_GROUPS for k in group):
        return "environment variables (MYSQL_* / DB_*)"
    if _on_render():
        return "Render Environment (MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE)"
    root_env = os.path.join(_project_root(), ".env")
    if os.path.exists(root_env):
        return "project .env"
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db_config.env")
    if os.path.exists(cfg_path):
        return "database/db_config.env"
    return "MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE (or DATABASE_URL)"


def reset_config():
    """Clear cached config (tests)."""
    global _CONFIG
    _CONFIG = None
