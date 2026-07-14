"""MySQL connection configuration for GurmadNet AI."""
import os

_CONFIG = None


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
            cfg[key.strip()] = value.strip()
    return cfg


def load_config():
    """Load DB settings from db_config.env and environment variables."""
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG.copy()

    base = os.path.dirname(os.path.abspath(__file__))
    file_cfg = _parse_env_file(os.path.join(base, "db_config.env"))

    def _get(*keys, default=""):
        for key in keys:
            if key in os.environ:
                return os.environ[key]
            if key in file_cfg:
                return file_cfg[key]
        return default

    _CONFIG = {
        "host": _get("DB_HOST", "MYSQL_HOST", default="127.0.0.1"),
        "port": int(_get("DB_PORT", "MYSQL_PORT", default="3306")),
        "user": _get("DB_USER", "MYSQL_USER", default="root"),
        "password": _get("DB_PASSWORD", "MYSQL_PASSWORD", default=""),
        "database": _get("DB_NAME", "MYSQL_DATABASE", default="gurmad"),
        "charset": "utf8mb4",
        "autocommit": True,
    }
    return _CONFIG.copy()


def reset_config():
    """Clear cached config (tests)."""
    global _CONFIG
    _CONFIG = None
