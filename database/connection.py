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

Stability (Render → Railway public TCP proxy):
  MYSQL_CONNECT_TIMEOUT / MYSQL_READ_TIMEOUT / MYSQL_WRITE_TIMEOUT
  MYSQL_SSL=auto|true|false  (auto enables TLS for non-localhost hosts)
"""
from __future__ import annotations

import os
import ssl
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
            "Set MYSQL_HOST to your Railway MySQL public proxy "
            "(e.g. sakura.proxy.rlwy.net) or DATABASE_URL."
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
        # Cross-cloud (Render ↔ Railway proxy) needs generous timeouts,
        # but keep connect_timeout short enough that retries finish before gunicorn.
        "connect_timeout": _int_env(
            "MYSQL_CONNECT_TIMEOUT", "DB_CONNECT_TIMEOUT", default=10, minimum=3, maximum=60
        ),
        "read_timeout": _int_env(
            "MYSQL_READ_TIMEOUT", "DB_READ_TIMEOUT", default=30, minimum=5, maximum=120
        ),
        "write_timeout": _int_env(
            "MYSQL_WRITE_TIMEOUT", "DB_WRITE_TIMEOUT", default=30, minimum=5, maximum=120
        ),
        # Avoid 2013 on larger JSON / BLOB-ish payloads over the proxy.
        "max_allowed_packet": 64 * 1024 * 1024,
    }
    ssl_opt = _ssl_connect_arg(host)
    if ssl_opt is not None:
        _CONFIG["ssl"] = ssl_opt
    return _CONFIG.copy()


def _int_env(*keys, default, minimum=None, maximum=None):
    raw = ""
    for key in keys:
        raw = str(os.environ.get(key) or "").strip()
        if raw:
            break
    try:
        value = int(raw) if raw else int(default)
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _is_local_mysql_host(host):
    h = (host or "").strip().lower()
    return h in ("", "127.0.0.1", "localhost", "::1")


def _is_railway_tcp_proxy(host):
    """Railway public TCP proxy speaks plain MySQL — client TLS causes WRONG_VERSION_NUMBER."""
    h = (host or "").strip().lower()
    return h.endswith(".proxy.rlwy.net") or h.endswith(".rlwy.net")


def _ssl_connect_arg(host):
    """
    TLS for remote MySQL.

    MYSQL_SSL=auto (default):
      - off for localhost
      - off for Railway public TCP proxy (*.proxy.rlwy.net) — proxy is not TLS
      - on for other remote hosts
    MYSQL_SSL=true|require|1|on: always TLS
    MYSQL_SSL=false|0|off|disable: never TLS

    When TLS is enabled without a pinned CA, use CERT_NONE (encrypted, unverified).
    """
    mode = (os.environ.get("MYSQL_SSL") or os.environ.get("DB_SSL") or "auto").strip().lower()
    if mode in ("0", "false", "off", "no", "disable", "disabled"):
        return None
    force = mode in ("1", "true", "yes", "on", "require", "required", "force")
    if not force:
        # auto / unknown → skip local and Railway public proxy
        if _is_local_mysql_host(host) or _is_railway_tcp_proxy(host):
            return None

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


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


def safe_config_summary(cfg=None):
    """Return a log-safe dict of connection settings (never includes the password)."""
    if cfg is None:
        try:
            cfg = load_config()
        except Exception as exc:
            return {"error": str(exc), "source": config_source_hint()}
    password = cfg.get("password") or ""
    ssl_val = cfg.get("ssl")
    return {
        "host": cfg.get("host"),
        "port": cfg.get("port"),
        "user": cfg.get("user"),
        "database": cfg.get("database"),
        "password_set": bool(str(password).strip()),
        "password_len": len(str(password)),
        "ssl": bool(ssl_val),
        "autocommit": cfg.get("autocommit"),
        "connect_timeout": cfg.get("connect_timeout"),
        "read_timeout": cfg.get("read_timeout"),
        "write_timeout": cfg.get("write_timeout"),
        "source": config_source_hint(),
    }


def reset_config():
    """Clear cached config (tests)."""
    global _CONFIG
    _CONFIG = None
