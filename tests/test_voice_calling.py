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
