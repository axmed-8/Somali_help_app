"""Server-side tests for in-app WebRTC voice call session lifecycle."""
from __future__ import annotations

import call_center_logic as cc


def _mem():
    store = {"call_center_calls": {"calls": [], "next_id": 1}}

    def read_fn(entity, default=None):
        return store.get(entity, default if default is not None else {})

    def save_fn(entity, data):
        store[entity] = data

    return read_fn, save_fn, store


def test_claim_voice_call_exclusive():
    read_fn, save_fn, _ = _mem()
    call = cc.create_incoming_call(
        {
            "user_id": 10,
            "name": "Citizen A",
            "phone": "615000000",
            "latitude": 2.04,
            "longitude": 45.34,
            "voice_mode": True,
        },
        read_fn,
        save_fn,
        stations=[],
    )
    op1 = {"id": 1, "name": "Op One", "role": "call_center"}
    op2 = {"id": 2, "name": "Op Two", "role": "call_center"}
    claimed = cc.claim_voice_call(call["id"], op1, read_fn, save_fn)
    assert claimed["status"] == "connecting"
    assert claimed["operator_id"] == 1
    try:
        cc.claim_voice_call(call["id"], op2, read_fn, save_fn)
        assert False, "second operator should not claim"
    except ValueError as exc:
        assert "another operator" in str(exc).lower() or "not available" in str(exc).lower()


def test_operator_busy_blocks_second_accept():
    read_fn, save_fn, _ = _mem()
    c1 = cc.create_incoming_call(
        {
            "user_id": 10,
            "name": "A",
            "phone": "1",
            "latitude": 2.0,
            "longitude": 45.0,
            "voice_mode": True,
        },
        read_fn,
        save_fn,
        stations=[],
    )
    c2 = cc.create_incoming_call(
        {
            "user_id": 11,
            "name": "B",
            "phone": "2",
            "latitude": 2.1,
            "longitude": 45.1,
            "voice_mode": True,
        },
        read_fn,
        save_fn,
        stations=[],
    )
    op = {"id": 5, "name": "Busy Op", "role": "call_center"}
    cc.claim_voice_call(c1["id"], op, read_fn, save_fn)
    try:
        cc.claim_voice_call(c2["id"], op, read_fn, save_fn)
        assert False, "busy operator should not accept second call"
    except ValueError as exc:
        assert "already on another call" in str(exc).lower()


def test_end_voice_call_sync_fields():
    read_fn, save_fn, _ = _mem()
    call = cc.create_incoming_call(
        {
            "user_id": 10,
            "name": "Citizen A",
            "phone": "615000000",
            "latitude": 2.04,
            "longitude": 45.34,
            "voice_mode": True,
        },
        read_fn,
        save_fn,
        stations=[],
    )
    op = {"id": 1, "name": "Op", "role": "call_center"}
    cc.claim_voice_call(call["id"], op, read_fn, save_fn)
    cc.mark_voice_connected(call["id"], read_fn, save_fn)
    ended = cc.end_voice_call(
        call["id"],
        ended_by="operator",
        final_status="ended",
        read_fn=read_fn,
        save_fn=save_fn,
        operator=op,
    )
    assert ended["status"] == "ended"
    assert ended["ended_by"] == "operator"
    assert ended.get("end_time")
    # Idempotent
    again = cc.end_voice_call(
        call["id"],
        ended_by="citizen",
        final_status="ended",
        read_fn=read_fn,
        save_fn=save_fn,
    )
    assert again["ended_by"] == "operator"


def test_answer_call_preserves_voice_connecting_status():
    """REST /answer must not downgrade an in-progress WebRTC session to legacy 'answered'."""
    read_fn, save_fn, _ = _mem()
    call = cc.create_incoming_call(
        {
            "user_id": 10,
            "name": "Citizen A",
            "phone": "615000000",
            "latitude": 2.04,
            "longitude": 45.34,
            "voice_mode": True,
        },
        read_fn,
        save_fn,
        stations=[],
    )
    op = {"id": 1, "name": "Op", "role": "call_center"}
    cc.claim_voice_call(call["id"], op, read_fn, save_fn)
    assert cc.get_call_by_id(read_fn("call_center_calls"), call["id"])["status"] == "connecting"
    answered = cc.answer_call(call["id"], op, read_fn, save_fn, stations=[])
    assert answered["status"] == "connecting"


def test_ice_servers_stun_default_and_turn_env(monkeypatch):
    import voice_signaling as vs

    monkeypatch.delenv("WEBRTC_STUN_URLS", raising=False)
    monkeypatch.delenv("WEBRTC_STUN_URL", raising=False)
    monkeypatch.delenv("TURN_URLS", raising=False)
    monkeypatch.delenv("WEBRTC_TURN_URL", raising=False)
    monkeypatch.delenv("WEBRTC_TURN_URLS", raising=False)
    monkeypatch.delenv("TURN_USERNAME", raising=False)
    monkeypatch.delenv("TURN_CREDENTIAL", raising=False)
    monkeypatch.delenv("WEBRTC_TURN_USERNAME", raising=False)
    monkeypatch.delenv("WEBRTC_TURN_CREDENTIAL", raising=False)
    servers = vs.ice_servers()
    assert any("stun:" in str(s.get("urls")) for s in servers)
    assert not any("turn:" in str(s.get("urls")).lower() for s in servers)
    summary = vs.ice_config_summary()
    assert summary["stun"] is True
    assert summary["turn"] is False
    assert "credential" not in summary

    monkeypatch.setenv("WEBRTC_STUN_URL", "stun:stun.l.google.com:19302")
    monkeypatch.setenv("WEBRTC_TURN_URL", "turn:turn.example:3478,turns:turn.example:443")
    monkeypatch.setenv("WEBRTC_TURN_USERNAME", "u1")
    monkeypatch.setenv("WEBRTC_TURN_CREDENTIAL", "p1")
    servers2 = vs.ice_servers()
    assert any("stun:" in str(s.get("urls")) for s in servers2)
    turn = [s for s in servers2 if "turn" in str(s.get("urls")).lower()]
    assert turn
    assert turn[0]["username"] == "u1"
    assert turn[0]["credential"] == "p1"
    summary2 = vs.ice_config_summary()
    assert summary2["stun"] and summary2["turn"] and summary2["turn_auth"]
    assert "p1" not in str(summary2)


def test_parse_database_url_and_render_rejects_localhost(monkeypatch):
    from database.connection import (
        load_config,
        mysql_credentials_present,
        parse_database_url,
        reset_config,
    )

    parsed = parse_database_url("mysql://u:p%40ss@db.example:3307/gurmad")
    assert parsed["host"] == "db.example"
    assert parsed["port"] == 3307
    assert parsed["user"] == "u"
    assert parsed["password"] == "p@ss"
    assert parsed["database"] == "gurmad"

    monkeypatch.setenv("RENDER", "true")
    for k in (
        "MYSQL_HOST",
        "DB_HOST",
        "MYSQL_USER",
        "DB_USER",
        "MYSQL_PASSWORD",
        "DB_PASSWORD",
        "MYSQL_DATABASE",
        "DB_NAME",
        "DATABASE_URL",
        "MYSQL_URL",
    ):
        monkeypatch.delenv(k, raising=False)
    reset_config()
    assert not mysql_credentials_present()
    monkeypatch.setenv("DATABASE_URL", "mysql://gurmad:secret@gurmadnet-mysql:3306/gurmad")
    reset_config()
    assert mysql_credentials_present()
    cfg = load_config()
    assert cfg["host"] == "gurmadnet-mysql"
    assert cfg["user"] == "gurmad"
    assert cfg["database"] == "gurmad"
    assert cfg["password"] == "secret"

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("MYSQL_USER", "gurmad")
    monkeypatch.setenv("MYSQL_PASSWORD", "x")
    monkeypatch.setenv("MYSQL_DATABASE", "gurmad")
    reset_config()
    try:
        load_config()
        assert False, "expected localhost rejection on Render"
    except RuntimeError as exc:
        assert "localhost" in str(exc).lower() or "MYSQL_HOST" in str(exc)


def test_reject_removes_from_active_queue():
    read_fn, save_fn, _ = _mem()
    call = cc.create_incoming_call(
        {
            "user_id": 10,
            "name": "Citizen A",
            "phone": "615000000",
            "latitude": 2.04,
            "longitude": 45.34,
            "voice_mode": True,
        },
        read_fn,
        save_fn,
        stations=[],
    )
    op = {"id": 1, "name": "Op", "role": "call_center"}
    ended = cc.end_voice_call(
        call["id"],
        ended_by="operator",
        final_status="rejected",
        read_fn=read_fn,
        save_fn=save_fn,
        operator=op,
    )
    assert ended["status"] == "rejected"
    active = cc.active_calls(read_fn, save_fn)
    assert not any(c.get("id") == call["id"] for c in active)
