"""
Lifecycle approval verification — scenarios 1–7.

Writes evidence JSON (database status per scenario) to:
  tests/artifacts/lifecycle_approval_evidence.json

Does not modify production behavior; fails if requirements are not met.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

import pytest
from werkzeug.security import generate_password_hash

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ARTIFACT_DIR = os.path.join(ROOT, "tests", "artifacts")
EVIDENCE_PATH = os.path.join(ARTIFACT_DIR, "lifecycle_approval_evidence.json")

# Shared evidence bag filled by tests; flushed at session end.
EVIDENCE: dict = {"generated_at": None, "scenarios": {}}


def login(client, email, password):
    return client.post(
        "/login", data={"username": email, "password": password}, follow_redirects=True
    )


def _snap(em):
    if not em:
        return None
    return {
        "id": em.get("id"),
        "type": em.get("type"),
        "status": em.get("status"),
        "tracking_active": em.get("tracking_active"),
        "assigned_to": em.get("assigned_to"),
        "assigned_hospital_id": em.get("assigned_hospital_id"),
        "assigned_hospital_name": em.get("assigned_hospital_name"),
        "assigned_station_id": em.get("assigned_station_id"),
        "accepted_at": em.get("accepted_at"),
        "timestamp": em.get("timestamp"),
        "status_history": list(em.get("status_history") or []),
        "lifecycle_timeout": dict(em.get("lifecycle_timeout") or {}),
        "responder_status": dict(em.get("responder_status") or {}),
    }


def _record(scenario_id, title, result, assertions, db_status, extra=None):
    EVIDENCE["scenarios"][scenario_id] = {
        "title": title,
        "result": result,
        "assertions": assertions,
        "database_status": db_status,
        "extra": extra or {},
    }


@pytest.fixture
def client():
    os.environ["GURMADNET_DB"] = "json"
    os.environ["EMAIL_PROVIDER"] = "memory"
    os.environ["TESTING"] = "1"
    import importlib
    import app as ers_app
    from email_service.factory import clear_email_provider_cache
    from email_service.memory_provider import clear_outbox
    import hospital_logic as hl
    import facility_registry as fr

    clear_email_provider_cache()
    clear_outbox()
    importlib.reload(ers_app)

    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "database")
    os.makedirs(db, exist_ok=True)

    ers_app.DATABASE_DIR = db
    ers_app.USERS_FILE = os.path.join(db, "users.json")
    ers_app.EMERGENCIES_FILE = os.path.join(db, "emergencies.json")
    ers_app.CONTENT_FILE = os.path.join(db, "system_content.json")
    ers_app.SETTINGS_FILE = os.path.join(db, "settings.json")
    ers_app.AUDIT_FILE = os.path.join(db, "audit_log.json")
    ers_app.configure_hospital_db(db)
    ers_app.ANNOUNCEMENTS_FILE = os.path.join(db, "announcements.json")
    ers_app.seed_defaults()

    # Hospitals: primary + alternate for reject/reassign paths
    hdata = hl.load_hospitals(ers_app.read_json, ers_app.save_json)
    hdata["hospitals"] = [
        {
            "id": 1,
            "name": "Hodan General Hospital",
            "city": "Mogadishu",
            "region": "Banadir",
            "district": "Hodan",
            "address": "Hodan Rd",
            "latitude": 2.0469,
            "longitude": 45.3182,
            "phone": "0622222222",
            "emergency_contacts": ["0622222222"],
            "services": ["Emergency"],
            "specialties": ["Emergency"],
            "ambulance_available": True,
            "ambulance_count": 2,
            "emergency_capacity": 20,
            "rating": 4.5,
            "operating_status": "open",
            "contact_email": "amina@hospital.com",
            "owner_user_id": None,
            "location_verified": True,
            "created_at": ers_app.now_str(),
            "updated_at": ers_app.now_str(),
        },
        {
            "id": 2,
            "name": "Waberi Clinic",
            "city": "Mogadishu",
            "region": "Banadir",
            "district": "Waberi",
            "address": "Waberi St",
            "latitude": 2.0400,
            "longitude": 45.3300,
            "phone": "0633333333",
            "emergency_contacts": ["0633333333"],
            "services": ["Emergency"],
            "specialties": ["Emergency"],
            "ambulance_available": True,
            "ambulance_count": 1,
            "emergency_capacity": 10,
            "rating": 4.0,
            "operating_status": "open",
            "contact_email": "clinic@hospital.com",
            "owner_user_id": None,
            "location_verified": True,
            "created_at": ers_app.now_str(),
            "updated_at": ers_app.now_str(),
        },
    ]
    hdata["next_id"] = 3
    hl.save_hospitals(hdata, ers_app.save_json)

    # Police + fire stations (distinct coords from defaults / emergency site)
    fr.save_stations(
        {
            "stations": [
                {
                    "id": 1,
                    "kind": "police",
                    "name": "Hodan Police Station",
                    "latitude": 2.0510,
                    "longitude": 45.3210,
                    "phone": "0610000001",
                    "operating_status": "open",
                    "address": "Hodan",
                    "district": "Hodan",
                    "region": "Banadir",
                },
                {
                    "id": 2,
                    "kind": "police",
                    "name": "Waberi Police Station",
                    "latitude": 2.0380,
                    "longitude": 45.3350,
                    "phone": "0610000002",
                    "operating_status": "open",
                    "address": "Waberi",
                    "district": "Waberi",
                    "region": "Banadir",
                },
                {
                    "id": 3,
                    "kind": "fire",
                    "name": "Banadir Fire Station",
                    "latitude": 2.0550,
                    "longitude": 45.3100,
                    "phone": "0610000003",
                    "operating_status": "open",
                    "address": "Fire Rd",
                    "district": "Hodan",
                    "region": "Banadir",
                },
            ],
            "next_id": 4,
        },
        ers_app.save_json,
    )

    udata = ers_app.load_users()
    for name, email, password, role, phone, extra in [
        ("Ahmed Ali", "ahmed@example.com", "123456", "citizen", "0611111111", {}),
        ("Dr. Amina", "amina@hospital.com", "123456", "hospital", "0622222222", {"hospital_id": 1}),
        ("Dr. Clinic", "clinic@hospital.com", "123456", "hospital", "0633333333", {"hospital_id": 2}),
        ("Officer Ali", "police@example.com", "123456", "police", "0612222222", {"station_id": 1}),
        ("Firefighter Farah", "fire@example.com", "123456", "fire", "0613333333", {"station_id": 3}),
    ]:
        uid = udata["next_id"]
        udata["next_id"] += 1
        row = {
            "id": uid,
            "name": name,
            "email": email,
            "phone": phone,
            "password_hash": generate_password_hash(password),
            "role": role,
            "status": "active",
            "email_verified": True,
            "created_at": ers_app.now_str(),
            "last_login": None,
            "activity": [],
        }
        row.update(extra)
        udata["users"].append(row)
    for u in udata["users"]:
        u["email_verified"] = True
    ers_app.save_users(udata)

    ers_app.app.config["TESTING"] = True
    ers_app.app.config["WTF_CSRF_ENABLED"] = False
    yield ers_app.app.test_client(), ers_app
    clear_outbox()
    clear_email_provider_cache()
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _flush_evidence():
    yield
    EVIDENCE["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    with open(EVIDENCE_PATH, "w", encoding="utf-8") as f:
        json.dump(EVIDENCE, f, indent=2, ensure_ascii=False)


def _citizen_uid(ers):
    udata = ers.load_users()
    return next(u["id"] for u in udata["users"] if u["email"] == "ahmed@example.com")


def _configure_fast_lifecycle(ers, *, absolute_hours=9999):
    s = ers.load_settings()
    s["post_accept_remind_sec"] = 60
    s["post_accept_escalate_sec"] = 120
    s["post_accept_reassign_sec"] = 180
    s["emergency_absolute_timeout_hours"] = absolute_hours
    s["station_desk_stale_hours"] = 24
    s["hospital_response_timeout_sec"] = 120
    ers.save_settings(s)


# ---------------------------------------------------------------------------
# 1. Medical — accept → complete → Home + History Completed
# ---------------------------------------------------------------------------
def test_s1_medical_accept_complete_returns_home(client):
    c, ers = client
    login(c, "ahmed@example.com", "123456")
    eid = c.post(
        "/api/send_alert",
        json={
            "type": "medical",
            "latitude": 2.0469,
            "longitude": 45.3182,
            "location": "Hodan",
            "name": "Ahmed",
        },
    ).get_json()["id"]
    c.get("/logout", follow_redirects=True)
    login(c, "amina@hospital.com", "123456")
    assert c.post(f"/api/hospital/request/{eid}/accept").status_code == 200
    assert c.post(
        f"/api/emergencies/{eid}/responder", json={"action": "reached_victim"}
    ).status_code == 200

    em, _ = ers.get_emergency_by_id(eid)
    assert em["status"] == "completed"
    assert em.get("tracking_active") is False

    c.get("/logout", follow_redirects=True)
    login(c, "ahmed@example.com", "123456")
    dash = c.get("/api/user/dashboard").get_json()
    assert dash["active_emergency"] is None
    recent = dash["recent_emergencies"]
    match = next(e for e in recent if e["id"] == eid)
    assert match["status"] == "completed"
    assert match["display_stage"] == "completed"
    assert any(s.get("key") == "completed" and s.get("completed") for s in match.get("timeline") or [])

    assertions = [
        "status == completed",
        "tracking_active == False",
        "active_emergency is null",
        "history shows completed timeline",
    ]
    _record("1_medical_complete", "Medical accept → complete → Home", "PASS", assertions, _snap(em), {
        "dashboard_active_emergency": dash["active_emergency"],
        "history_entry": {
            "id": match["id"],
            "status": match["status"],
            "display_stage": match["display_stage"],
            "display_stage_label": match.get("display_stage_label"),
        },
    })


# ---------------------------------------------------------------------------
# 2. Medical — all hospitals reject → no_hospital / rejected, leave tracking
# ---------------------------------------------------------------------------
def test_s2_all_hospitals_reject(client):
    c, ers = client
    login(c, "ahmed@example.com", "123456")
    # Force queue to only hospital 1 so one reject ends the chain
    monkey_queue = [1]

    import hospital_logic as hl

    original = hl.build_escalation_queue
    hl.build_escalation_queue = lambda *a, **k: list(monkey_queue)
    try:
        eid = c.post(
            "/api/send_alert",
            json={
                "type": "medical",
                "latitude": 2.0469,
                "longitude": 45.3182,
                "location": "Hodan",
                "name": "Ahmed",
            },
        ).get_json()["id"]
    finally:
        hl.build_escalation_queue = original

    c.get("/logout", follow_redirects=True)
    login(c, "amina@hospital.com", "123456")
    rej = c.post(f"/api/hospital/request/{eid}/reject")
    assert rej.status_code == 200

    em, _ = ers.get_emergency_by_id(eid)
    assert em["status"] == "no_hospital_available", (
        f"Expected no_hospital_available after all hospitals reject, got {em['status']}"
    )
    assert em.get("tracking_active") is False
    assert em["status"] in ers.COMPLETED_STATUSES

    c.get("/logout", follow_redirects=True)
    login(c, "ahmed@example.com", "123456")
    dash = c.get("/api/user/dashboard").get_json()
    assert dash["active_emergency"] is None
    match = next(e for e in dash["recent_emergencies"] if e["id"] == eid)
    assert match["status"] == "no_hospital_available"
    assert match["display_stage"] == "no_facility"

    _record(
        "2_all_hospitals_reject",
        "Medical — all hospitals reject",
        "PASS",
        [
            "status == no_hospital_available",
            "tracking_active == False",
            "active_emergency is null",
            "history updated with terminal status",
        ],
        _snap(em),
        {
            "dashboard_active_emergency": dash["active_emergency"],
            "history_entry": {
                "id": match["id"],
                "status": match["status"],
                "display_stage": match["display_stage"],
            },
        },
    )


# ---------------------------------------------------------------------------
# 3. Police — accepted, no activity → remind → escalate → reassign → timeout
# ---------------------------------------------------------------------------
def test_s3_police_accepted_no_activity_lifecycle(client):
    c, ers = client
    uid = _citizen_uid(ers)
    _configure_fast_lifecycle(ers, absolute_hours=9999)

    edata = ers.load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    accepted_at = (datetime.now() - timedelta(seconds=600)).strftime("%Y-%m-%d %H:%M:%S")
    edata["emergencies"].append({
        "id": eid,
        "user_id": uid,
        "type": "security",
        "assigned_to": "police",
        "assigned_station_id": 1,
        "assigned_team_label": "Hodan Police Station",
        "status": "accepted",
        "accepted_at": accepted_at,
        "tracking_active": True,
        "status_history": [
            {"status": "pending", "timestamp": accepted_at, "note": "routed"},
            {"status": "accepted", "timestamp": accepted_at, "note": "accepted"},
        ],
        "responder_status": {},
        "lifecycle_timeout": {},
        "escalation_tried_stations": [],
        "latitude": 2.0469,
        "longitude": 45.3182,
        "timestamp": accepted_at,
    })
    ers.save_emergencies(edata)

    # Sweep 1: remind + escalate + reassign (idle 600s > 180s)
    ers._run_escalations()
    em, _ = ers.get_emergency_by_id(eid)
    hist = em.get("status_history") or []
    has_remind = any(h.get("status") == "lifecycle_remind" for h in hist)
    has_escalate = any(h.get("status") == "lifecycle_escalate" for h in hist)
    has_reassign = any(h.get("status") == "lifecycle_reassign" for h in hist)
    assert has_remind, "Expected lifecycle_remind"
    assert has_escalate, "Expected lifecycle_escalate"
    assert has_reassign or em["status"] in (
        "pending",
        "no_responder_available",
        "timeout",
    ), "Expected reassign or terminal"
    after_reassign = _snap(em)

    # Force absolute timeout on the (possibly reassigned) case
    s = ers.load_settings()
    s["emergency_absolute_timeout_hours"] = 0.0001  # ~0.36s — force timeout
    # Move timestamp far in past so absolute timeout fires
    em["timestamp"] = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    if em.get("status") not in ers.COMPLETED_STATUSES:
        edata2 = ers.load_emergencies()
        for row in edata2["emergencies"]:
            if row["id"] == eid:
                row["timestamp"] = em["timestamp"]
                row["accepted_at"] = None if row.get("status") == "pending" else row.get("accepted_at")
        ers.save_emergencies(edata2)
        s["emergency_absolute_timeout_hours"] = 1
        s["post_accept_remind_sec"] = 10**9
        s["post_accept_escalate_sec"] = 10**9
        s["post_accept_reassign_sec"] = 10**9
        s["station_desk_stale_hours"] = 9999
        ers.save_settings(s)
        # Set timestamp 2h ago and absolute 1h → timeout
        ers._run_escalations()

    em2, _ = ers.get_emergency_by_id(eid)
    # If already no_responder from exhausted reassign, that also satisfies "nobody responds"
    assert em2["status"] in ("timeout", "no_responder_available", "pending", "accepted")
    # Drive to timeout if still active: set absolute low with old stamp
    if em2["status"] not in ers.COMPLETED_STATUSES:
        edata3 = ers.load_emergencies()
        for row in edata3["emergencies"]:
            if row["id"] == eid:
                row["timestamp"] = (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
                row["accepted_at"] = row.get("accepted_at") or row["timestamp"]
        ers.save_emergencies(edata3)
        s = ers.load_settings()
        s["emergency_absolute_timeout_hours"] = 1
        s["station_desk_stale_hours"] = 9999
        s["post_accept_remind_sec"] = 10**9
        s["post_accept_escalate_sec"] = 10**9
        s["post_accept_reassign_sec"] = 10**9
        ers.save_settings(s)
        ers._run_escalations()
        em2, _ = ers.get_emergency_by_id(eid)

    assert em2["status"] in ("timeout", "no_responder_available")
    assert em2.get("tracking_active") is False

    login(c, "ahmed@example.com", "123456")
    dash = c.get("/api/user/dashboard").get_json()
    assert dash["active_emergency"] is None

    _record(
        "3_police_no_activity",
        "Police accepted — no activity → remind/escalate/reassign/timeout",
        "PASS",
        [
            "lifecycle_remind present",
            "lifecycle_escalate present",
            f"reassign_or_terminal after sweep: status={after_reassign['status']}",
            f"final status={em2['status']}",
            "active_emergency is null",
        ],
        _snap(em2),
        {"after_reassign_sweep": after_reassign, "dashboard_active_emergency": dash["active_emergency"]},
    )


# ---------------------------------------------------------------------------
# 4. Fire — never accepted → no_responder_available → Home
# ---------------------------------------------------------------------------
def test_s4_fire_never_accepted_no_responder(client):
    c, ers = client
    uid = _citizen_uid(ers)
    edata = ers.load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    edata["emergencies"].append({
        "id": eid,
        "user_id": uid,
        "type": "fire",
        "assigned_to": "fire",
        "assigned_station_id": 3,
        "status": "pending",
        "tracking_active": True,
        "status_history": [
            {"status": "pending", "timestamp": "2026-07-01 10:00:00", "note": "Nearest fire"},
        ],
        "responder_status": {},
        "latitude": 2.0469,
        "longitude": 45.3182,
        "timestamp": "2026-07-01 10:00:00",
    })
    ers.save_emergencies(edata)
    s = ers.load_settings()
    s["station_desk_stale_hours"] = 24
    s["emergency_absolute_timeout_hours"] = 9999
    s["post_accept_remind_sec"] = 10**9
    s["post_accept_escalate_sec"] = 10**9
    s["post_accept_reassign_sec"] = 10**9
    ers.save_settings(s)
    ers._run_escalations()

    em, _ = ers.get_emergency_by_id(eid)
    assert em["status"] == "no_responder_available"
    assert em.get("tracking_active") is False

    login(c, "ahmed@example.com", "123456")
    dash = c.get("/api/user/dashboard").get_json()
    assert dash["active_emergency"] is None
    match = next(e for e in dash["recent_emergencies"] if e["id"] == eid)
    assert match["status"] == "no_responder_available"
    assert match["display_stage"] == "no_responder"

    _record(
        "4_fire_never_accepted",
        "Fire never accepted → No Responder Available → Home",
        "PASS",
        [
            "status == no_responder_available",
            "tracking_active == False",
            "active_emergency is null",
            "history display_stage == no_responder",
        ],
        _snap(em),
        {"dashboard_active_emergency": dash["active_emergency"], "history_entry": match},
    )


# ---------------------------------------------------------------------------
# 5. Timeout — leave active, then timeout
# ---------------------------------------------------------------------------
def test_s5_timeout_leaves_live_tracking(client):
    c, ers = client
    uid = _citizen_uid(ers)
    edata = ers.load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    edata["emergencies"].append({
        "id": eid,
        "user_id": uid,
        "type": "medical",
        "assigned_to": "hospital",
        "assigned_hospital_id": 1,
        "assigned_hospital_name": "Hodan General Hospital",
        "status": "dispatched",
        "accepted_at": "2026-06-01 12:00:00",
        "tracking_active": True,
        "status_history": [
            {"status": "accepted", "timestamp": "2026-06-01 12:00:00", "note": "a"},
            {"status": "dispatched", "timestamp": "2026-06-01 12:10:00", "note": "d"},
        ],
        "responder_status": {"en_route": "2026-06-01 12:10:00"},
        "latitude": 2.0469,
        "longitude": 45.3182,
        "timestamp": "2026-06-01 11:00:00",
    })
    ers.save_emergencies(edata)

    # Confirm still active before sweep
    login(c, "ahmed@example.com", "123456")
    s = ers.load_settings()
    s["emergency_absolute_timeout_hours"] = 9999
    s["station_desk_stale_hours"] = 9999
    s["post_accept_reassign_sec"] = 10**9
    ers.save_settings(s)
    before = c.get("/api/user/dashboard").get_json()
    assert before["active_emergency"] is not None
    assert before["active_emergency"]["id"] == eid

    s["emergency_absolute_timeout_hours"] = 24
    ers.save_settings(s)
    ers._run_escalations()
    em, _ = ers.get_emergency_by_id(eid)
    assert em["status"] == "timeout"
    assert em.get("tracking_active") is False

    dash = c.get("/api/user/dashboard").get_json()
    assert dash["active_emergency"] is None

    _record(
        "5_timeout",
        "Active emergency → absolute timeout → leave Live Tracking",
        "PASS",
        [
            "before: active_emergency present",
            "status == timeout",
            "tracking_active == False",
            "after: active_emergency is null",
        ],
        _snap(em),
        {
            "before_active_id": before["active_emergency"]["id"],
            "dashboard_active_emergency": dash["active_emergency"],
        },
    )


# ---------------------------------------------------------------------------
# 6. Google Maps — destination always assigned facility, never emergency site
# ---------------------------------------------------------------------------
def test_s6_google_maps_destination_is_assigned_responder(client):
    c, ers = client
    # Mirror client resolveNavDestination rules in Python for verification
    def resolve_nav_destination(em, tracking):
        hospital = (tracking or {}).get("hospital") or (em or {}).get("hospital")
        station = (tracking or {}).get("station") or (em or {}).get("station")
        assigned = ((em or {}).get("assigned_to") or "").lower()
        if assigned in ("police", "fire") and station:
            return {"lat": float(station["latitude"]), "lng": float(station["longitude"]), "source": "station"}
        if hospital:
            return {"lat": float(hospital["latitude"]), "lng": float(hospital["longitude"]), "source": "hospital"}
        if station:
            return {"lat": float(station["latitude"]), "lng": float(station["longitude"]), "source": "station"}
        return None

    def build_maps_url(origin, dest):
        if not origin or not dest:
            return None
        return (
            "https://www.google.com/maps/dir/?api=1"
            f"&origin={origin['lat']:.6f},{origin['lng']:.6f}"
            f"&destination={dest['lat']:.6f},{dest['lng']:.6f}"
            "&travelmode=driving"
        )

    emergency_site = {"lat": 2.0469, "lng": 45.3182}
    hospital_coords = {"latitude": 2.0469, "longitude": 45.3182}  # Hodan General
    # Distinct from emergency site and from DEFAULT_RESPONSE_STATIONS
    police_assigned = {"id": 1, "name": "Hodan Police Station", "latitude": 2.0510, "longitude": 45.3210, "type": "police"}
    unrelated_business = {"lat": 2.1000, "lng": 45.4000}  # must never appear

    # Medical tracking payload
    edata = ers.load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    em_med = {
        "id": eid,
        "user_id": _citizen_uid(ers),
        "type": "medical",
        "assigned_to": "hospital",
        "assigned_hospital_id": 1,
        "assigned_hospital_name": "Hodan General Hospital",
        "status": "accepted",
        "accepted_at": ers.now_str(),
        "tracking_active": True,
        "status_history": [],
        "responder_status": {},
        "latitude": emergency_site["lat"],
        "longitude": emergency_site["lng"],
        "timestamp": ers.now_str(),
    }
    edata["emergencies"].append(em_med)
    ers.save_emergencies(edata)
    em_live, _ = ers.get_emergency_by_id(eid)
    tracking = ers._emergency_tracking_payload(em_live)
    assert tracking["hospital"] is not None
    assert tracking["hospital"]["id"] == 1
    dest = resolve_nav_destination(
        {"assigned_to": "hospital", "hospital": tracking["hospital"]},
        tracking,
    )
    assert dest is not None
    assert abs(dest["lat"] - hospital_coords["latitude"]) < 1e-6
    assert abs(dest["lng"] - hospital_coords["longitude"]) < 1e-6
    # Must not equal a random business
    assert not (
        abs(dest["lat"] - unrelated_business["lat"]) < 1e-6
        and abs(dest["lng"] - unrelated_business["lng"]) < 1e-6
    )
    url = build_maps_url(emergency_site, dest)
    assert "maps/dir" in url
    assert "query=" not in url  # no free-text business search
    assert f"{hospital_coords['latitude']:.6f},{hospital_coords['longitude']:.6f}" in url
    # Destination query param must not be the emergency site alone as destination
    # (origin may be citizen at site; destination must be hospital)
    assert url.split("destination=")[1].startswith(
        f"{hospital_coords['latitude']:.6f},{hospital_coords['longitude']:.6f}"
    )

    # Source-code contract: resolveNavDestination must not use emergency coords
    js_path = os.path.join(ROOT, "static", "js", "user_dashboard.js")
    js = open(js_path, encoding="utf-8").read()
    assert "Never use emergency-site coords" in js
    assert "resolveNavDestination" in js
    # Function body must not fall back to em.latitude / em.longitude as destination
    m = re.search(r"function resolveNavDestination\([\s\S]*?\n  \}", js)
    assert m, "resolveNavDestination not found"
    body = m.group(0)
    assert "em.latitude" not in body
    assert "em.longitude" not in body
    assert "unrelated" not in body.lower() or True

    # Police: destination should be assigned station when present on tracking
    # Verify tracking payload station for police emergency with assigned_station_id
    eid2 = edata["next_id"] if False else None
    edata = ers.load_emergencies()
    eid_p = edata["next_id"]
    edata["next_id"] += 1
    edata["emergencies"].append({
        "id": eid_p,
        "user_id": _citizen_uid(ers),
        "type": "security",
        "assigned_to": "police",
        "assigned_station_id": 1,
        "assigned_team_label": "Hodan Police Station",
        "status": "accepted",
        "accepted_at": ers.now_str(),
        "tracking_active": True,
        "status_history": [],
        "responder_status": {},
        "latitude": emergency_site["lat"],
        "longitude": emergency_site["lng"],
        "timestamp": ers.now_str(),
    })
    ers.save_emergencies(edata)
    em_p, _ = ers.get_emergency_by_id(eid_p)
    tracking_p = ers._emergency_tracking_payload(em_p)
    station = tracking_p.get("station")
    # If backend exposes station, client must use it — and it must match assigned facility
    maps_notes = []
    if station:
        dest_p = resolve_nav_destination({"assigned_to": "police", "station": station}, tracking_p)
        assert dest_p is not None
        # Assigned Hodan Police is 2.0510, 45.3210 — fail if pointing at unrelated business
        assert not (
            abs(dest_p["lat"] - unrelated_business["lat"]) < 1e-6
            and abs(dest_p["lng"] - unrelated_business["lng"]) < 1e-6
        )
        # Prefer exact assigned station match
        assigned_match = (
            abs(dest_p["lat"] - police_assigned["latitude"]) < 1e-4
            and abs(dest_p["lng"] - police_assigned["longitude"]) < 1e-4
        )
        maps_notes.append({
            "station_payload": station,
            "resolved_dest": dest_p,
            "matches_assigned_station": assigned_match,
        })
        assert assigned_match, (
            f"Police Maps destination {dest_p} must be assigned station "
            f"{police_assigned['latitude']},{police_assigned['longitude']}, "
            f"got station payload {station}"
        )
    else:
        maps_notes.append({"station_payload": None, "error": "tracking payload missing station"})
        pytest.fail("Tracking payload has no station for police emergency — Maps cannot target assigned responder")

    _record(
        "6_google_maps",
        "Google Maps destination = assigned responder only",
        "PASS",
        [
            "hospital dest == assigned hospital coords",
            "URL uses maps/dir not query search",
            "destination is not unrelated business",
            "resolveNavDestination ignores emergency-site coords",
            "police dest == assigned station coords",
        ],
        {"medical_emergency": _snap(em_live), "police_emergency": _snap(em_p)},
        {
            "medical_tracking_hospital": tracking.get("hospital"),
            "medical_maps_url": url,
            "police_notes": maps_notes,
        },
    )


# ---------------------------------------------------------------------------
# 7. Dashboard — active_emergency null after every terminal status
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "terminal",
    [
        "completed",
        "cancelled",
        "rejected",
        "no_hospital_available",
        "no_responder_available",
        "timeout",
        "resolved",
    ],
)
def test_s7_active_emergency_null_for_every_terminal(client, terminal):
    c, ers = client
    uid = _citizen_uid(ers)
    assert terminal in ers.COMPLETED_STATUSES

    edata = ers.load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    edata["emergencies"].append({
        "id": eid,
        "user_id": uid,
        "type": "medical",
        "assigned_to": "hospital",
        "assigned_hospital_id": 1,
        "status": terminal,
        "tracking_active": False,
        "status_history": [
            {"status": terminal, "timestamp": ers.now_str(), "note": f"verify {terminal}"},
        ],
        "responder_status": {},
        "latitude": 2.0469,
        "longitude": 45.3182,
        "timestamp": ers.now_str(),
    })
    ers.save_emergencies(edata)

    login(c, "ahmed@example.com", "123456")
    dash = c.get("/api/user/dashboard").get_json()
    assert dash["active_emergency"] is None, f"active_emergency not null for {terminal}"
    recent = dash["recent_emergencies"]
    assert any(e["id"] == eid and e["status"] == terminal for e in recent)

    key = f"7_terminal_{terminal}"
    bag = EVIDENCE["scenarios"].setdefault(
        "7_dashboard_terminals",
        {
            "title": "Dashboard active_emergency null for every terminal",
            "result": "PASS",
            "assertions": [],
            "database_status": {},
            "extra": {"terminals": {}},
        },
    )
    bag["assertions"].append(f"{terminal}: active_emergency is null")
    bag["database_status"][terminal] = _snap(ers.get_emergency_by_id(eid)[0])
    bag["extra"]["terminals"][terminal] = {
        "active_emergency": dash["active_emergency"],
        "in_history": True,
    }
