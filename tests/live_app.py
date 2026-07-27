"""Shared helpers for tests that need the live MySQL-backed app (not JSON isolation)."""
from __future__ import annotations

import importlib
import os


def reload_live_app():
    """
    Reset storage mode after JSON-isolation tests polluted os.environ / app module.
    Returns the reloaded app module.
    """
    os.environ.pop("GURMADNET_DB", None)
    # Keep pytest detection for JSON fallback if MySQL is down; prefer MySQL when available.
    os.environ["TESTING"] = "1"
    import app as ers_app

    importlib.reload(ers_app)
    ers_app.app.config["TESTING"] = True
    # CSRF still enabled — HTML pages get meta via after_request; APIs need X-CSRFToken.
    # Repair facility operator links so police/fire desks receive SOS cases.
    try:
        ers_app.ensure_mysql_boot()
    except Exception:
        pass
    return ers_app


def reload_json_app(monkeypatch=None, database_dir=None):
    """
    Force JSON file store for isolation tests.
    Must reload the app module so USE_MYSQL is recomputed after live tests.
    """
    if monkeypatch is not None:
        monkeypatch.setenv("TESTING", "1")
        monkeypatch.setenv("GURMADNET_DB", "json")
        if database_dir is not None:
            monkeypatch.setenv("DATABASE_DIR", str(database_dir))
    else:
        os.environ["TESTING"] = "1"
        os.environ["GURMADNET_DB"] = "json"
        if database_dir is not None:
            os.environ["DATABASE_DIR"] = str(database_dir)

    import app as ers_app

    importlib.reload(ers_app)
    if database_dir is not None:
        ers_app.DATABASE_DIR = str(database_dir)
        if hasattr(ers_app, "configure_hospital_db"):
            ers_app.configure_hospital_db(str(database_dir))
    ers_app.app.config["TESTING"] = True
    ers_app.app.config["WTF_CSRF_ENABLED"] = False
    return ers_app
