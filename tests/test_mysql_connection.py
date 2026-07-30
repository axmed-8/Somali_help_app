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
    assert cfg["connect_timeout"] == 30
    assert cfg["read_timeout"] == 60
    assert cfg["write_timeout"] == 60
    assert cfg["max_allowed_packet"] >= 16 * 1024 * 1024
    assert isinstance(cfg.get("ssl"), ssl.SSLContext)
    assert cfg["ssl"].verify_mode == ssl.CERT_NONE


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
