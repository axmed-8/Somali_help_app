"""PyMySQL connect kwargs for Render + Railway stability."""
from __future__ import annotations

import ssl


def test_load_config_includes_timeouts_and_autocommit(monkeypatch):
    from database.connection import load_config, reset_config

    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setenv("MYSQL_HOST", "sakura.proxy.rlwy.net")
    monkeypatch.setenv("MYSQL_PORT", "33388")
    monkeypatch.setenv("MYSQL_USER", "root")
    monkeypatch.setenv("MYSQL_PASSWORD", "secret")
    monkeypatch.setenv("MYSQL_DATABASE", "railway")
    monkeypatch.delenv("MYSQL_SSL", raising=False)
    monkeypatch.delenv("MYSQL_CONNECT_TIMEOUT", raising=False)
    reset_config()

    cfg = load_config()
    assert cfg["autocommit"] is True
    assert cfg["connect_timeout"] == 10
    assert cfg["read_timeout"] == 30
    assert cfg["write_timeout"] == 30
    assert cfg["max_allowed_packet"] >= 16 * 1024 * 1024
    # Railway public TCP proxy is plain MySQL — auto SSL must stay off
    assert "ssl" not in cfg


def test_railway_proxy_ssl_force_enables_tls(monkeypatch):
    from database.connection import load_config, reset_config

    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setenv("MYSQL_HOST", "sakura.proxy.rlwy.net")
    monkeypatch.setenv("MYSQL_PORT", "33388")
    monkeypatch.setenv("MYSQL_USER", "root")
    monkeypatch.setenv("MYSQL_PASSWORD", "secret")
    monkeypatch.setenv("MYSQL_DATABASE", "railway")
    monkeypatch.setenv("MYSQL_SSL", "true")
    reset_config()
    cfg = load_config()
    assert isinstance(cfg.get("ssl"), ssl.SSLContext)


def test_safe_config_summary_hides_password(monkeypatch):
    from database.connection import load_config, reset_config, safe_config_summary

    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setenv("MYSQL_HOST", "sakura.proxy.rlwy.net")
    monkeypatch.setenv("MYSQL_PORT", "33388")
    monkeypatch.setenv("MYSQL_USER", "root")
    monkeypatch.setenv("MYSQL_PASSWORD", "super-secret-password")
    monkeypatch.setenv("MYSQL_DATABASE", "railway")
    monkeypatch.delenv("MYSQL_SSL", raising=False)
    reset_config()
    summary = safe_config_summary(load_config())
    assert summary["host"] == "sakura.proxy.rlwy.net"
    assert summary["port"] == 33388
    assert summary["user"] == "root"
    assert summary["database"] == "railway"
    assert summary["password_set"] is True
    assert summary["ssl"] is False
    assert "super-secret-password" not in str(summary)
    assert "password" not in summary or summary.get("password") is None


def test_remote_non_railway_enables_ssl_by_default(monkeypatch):
    from database.connection import load_config, reset_config

    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setenv("MYSQL_HOST", "db.example.com")
    monkeypatch.setenv("MYSQL_USER", "root")
    monkeypatch.setenv("MYSQL_PASSWORD", "x")
    monkeypatch.setenv("MYSQL_DATABASE", "gurmad")
    monkeypatch.delenv("MYSQL_SSL", raising=False)
    reset_config()
    cfg = load_config()
    assert isinstance(cfg.get("ssl"), ssl.SSLContext)


def test_local_host_skips_ssl_by_default(monkeypatch):
    from database.connection import load_config, reset_config

    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("MYSQL_USER", "root")
    monkeypatch.setenv("MYSQL_PASSWORD", "x")
    monkeypatch.setenv("MYSQL_DATABASE", "gurmad")
    monkeypatch.delenv("MYSQL_SSL", raising=False)
    reset_config()

    cfg = load_config()
    assert "ssl" not in cfg


def test_mysql_ssl_false_disables_tls(monkeypatch):
    from database.connection import load_config, reset_config

    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setenv("MYSQL_HOST", "sakura.proxy.rlwy.net")
    monkeypatch.setenv("MYSQL_USER", "root")
    monkeypatch.setenv("MYSQL_PASSWORD", "x")
    monkeypatch.setenv("MYSQL_DATABASE", "railway")
    monkeypatch.setenv("MYSQL_SSL", "false")
    reset_config()

    cfg = load_config()
    assert "ssl" not in cfg


def test_connect_retries_transient_errors(monkeypatch):
    import pymysql

    from database import mysql_store

    calls = {"n": 0}

    class FakeConn:
        def ping(self, reconnect=True):
            return None

    def fake_connect(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query")
        return FakeConn()

    monkeypatch.setattr(mysql_store, "load_config", lambda: {
        "host": "h", "port": 1, "user": "u", "password": "p",
        "database": "d", "charset": "utf8mb4", "autocommit": True,
        "connect_timeout": 5, "read_timeout": 5, "write_timeout": 5,
    })
    monkeypatch.setattr(mysql_store.pymysql, "connect", fake_connect)
    monkeypatch.setattr(mysql_store.time, "sleep", lambda *_: None)

    conn = mysql_store.connect(retries=3)
    assert isinstance(conn, FakeConn)
    assert calls["n"] == 3


def test_request_scoped_reuses_and_closes(monkeypatch):
    from database import mysql_store

    created = {"n": 0}
    closed = {"n": 0}

    class FakeConn:
        def ping(self, reconnect=True):
            return None

        def close(self):
            closed["n"] += 1

    def fake_connect(**kwargs):
        created["n"] += 1
        return FakeConn()

    monkeypatch.setattr(mysql_store, "load_config", lambda: {
        "host": "h", "port": 1, "user": "u", "password": "p",
        "database": "d", "charset": "utf8mb4", "autocommit": True,
        "connect_timeout": 5, "read_timeout": 5, "write_timeout": 5,
    })
    monkeypatch.setattr(mysql_store.pymysql, "connect", fake_connect)
    mysql_store.close_request_connection()
    mysql_store.enable_request_scoped_connections()
    with mysql_store._db() as c1:
        with mysql_store._db() as c2:
            assert c1 is c2
    assert created["n"] == 1
    assert closed["n"] == 0  # still open until teardown
    mysql_store.close_request_connection()
    assert closed["n"] == 1
    assert getattr(mysql_store._local, "conn", None) is None
