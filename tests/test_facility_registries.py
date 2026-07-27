"""Facility registries + admin command workflow APIs (JSON test store)."""
import os
import tempfile

import pytest

os.environ["TESTING"] = "1"
os.environ["GURMADNET_DB"] = "json"
os.environ.setdefault("EMAIL_PROVIDER", "memory")
os.environ.setdefault("ALLOW_TEST_EMAILS", "1")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from tests.live_app import reload_json_app

    app_module = reload_json_app(monkeypatch, database_dir=str(tmp_path))
    # Seed super admin
    from werkzeug.security import generate_password_hash

    udata = {
        "users": [
            {
                "id": 1,
                "name": "Super Admin",
                "email": "super@example.com",
                "phone": "",
                "password_hash": generate_password_hash("Secret123!"),
                "role": "super_admin",
                "status": "active",
                "email_verified": True,
                "hospital_id": None,
                "station_id": None,
                "call_center_id": None,
                "created_at": "2026-01-01 00:00:00",
                "activity": [],
            }
        ],
        "next_id": 2,
    }
    app_module.save_json("users", udata)
    app_module.save_json("hospitals", {"hospitals": [], "next_id": 1})
    app_module.save_json("response_stations", {"stations": [], "next_id": 1})
    app_module.save_json("ambulance_units", {"ambulances": [], "next_id": 1})
    app_module.save_json("call_centers", {"call_centers": [], "next_id": 1})
    app_module.save_json("emergencies", {"emergencies": [], "next_id": 1})
    app_module.save_json("audit_log", {"entries": [], "next_id": 1})
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = 1
            sess["role"] = "super_admin"
            sess["name"] = "Super Admin"
            sess["email"] = "super@example.com"
        yield c


def test_hospital_crud(client):
    r = client.post(
        "/api/admin/hospitals",
        json={
            "name": "Test Hospital",
            "region": "Banadir",
            "district": "Hodan",
            "city": "Mogadishu",
            "address": "Hodan, Mogadishu",
            "phone": "612345678",
            "latitude": 2.04,
            "longitude": 45.33,
            "services": ["Emergency Care"],
            "operating_status": "open",
            "owner_name": "Hospital Admin",
            "owner_email": "hospital.admin@example.com",
            "owner_password": "Secret123!",
        },
    )
    assert r.status_code == 201
    body = r.get_json()
    hid = body["hospital"]["id"]
    assert body.get("owner", {}).get("email") == "hospital.admin@example.com"
    assert body["hospital"].get("owner_user_id") == body["owner"]["id"]
    r2 = client.get("/api/admin/hospitals")
    assert r2.status_code == 200
    assert any(h["id"] == hid for h in r2.get_json()["hospitals"])
    r3 = client.put(
        f"/api/admin/hospitals/{hid}",
        json={"operating_status": "limited", "name": "Test Hospital Updated"},
    )
    assert r3.status_code == 200
    assert r3.get_json()["hospital"]["operating_status"] == "limited"
    r4 = client.post(f"/api/admin/hospitals/{hid}/toggle")
    assert r4.status_code == 200
    r5 = client.delete(f"/api/admin/hospitals/{hid}")
    assert r5.status_code == 200


def test_station_and_ambulance_and_call_center(client):
    # station
    rs = client.post(
        "/api/admin/stations",
        json={
            "kind": "police",
            "name": "Wadajir Police",
            "city": "Mogadishu",
            "phone": "611111111",
            "latitude": 2.03,
            "longitude": 45.3,
        },
    )
    assert rs.status_code == 201
    sid = rs.get_json()["station"]["id"]
    # hospital for ambulance
    rh = client.post(
        "/api/admin/hospitals",
        json={
            "name": "Amb Host",
            "region": "Banadir",
            "district": "Hodan",
            "city": "Mogadishu",
            "address": "Hodan",
            "phone": "612222222",
            "latitude": 2.04,
            "longitude": 45.33,
            "services": ["Emergency Care"],
            "owner_name": "Amb Host Admin",
            "owner_email": "amb.host@example.com",
            "owner_password": "Secret123!",
        },
    )
    assert rh.status_code == 201
    body = rh.get_json()
    hid = body["hospital"]["id"]
    owner_id = body["owner"]["id"]

    # Admin cannot create fleet units
    blocked = client.post(
        "/api/admin/ambulances",
        json={"hospital_id": hid, "call_sign": "AMB-1", "status": "available", "driver_phone": "610000001"},
    )
    assert blocked.status_code == 403

    # Hospital owns ambulance dispatch essentials
    with client.session_transaction() as sess:
        sess["user_id"] = owner_id
        sess["role"] = "hospital"
        sess["name"] = "Amb Host Admin"
        sess["email"] = "amb.host@example.com"
    ra = client.post(
        "/api/hospital/ambulances",
        json={
            "call_sign": "AMB-1",
            "status": "available",
            "driver_name": "Driver One",
            "driver_phone": "610000001",
            "latitude": 2.041,
            "longitude": 45.331,
        },
    )
    assert ra.status_code == 201
    amb = ra.get_json()["ambulance"]
    assert amb["hospital_id"] == hid
    assert amb["driver_phone"] == "610000001"
    assert "plate_number" not in amb  # dispatch view only

    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["role"] = "super_admin"
        sess["name"] = "Super Admin"
        sess["email"] = "super@example.com"

    # call center
    rc = client.post(
        "/api/admin/call-centers",
        json={"name": "National CC", "city": "Mogadishu", "phone": "777", "latitude": 2.05, "longitude": 45.32},
    )
    assert rc.status_code == 201
    # list filters — admin read-only board sees hospital unit
    assert client.get("/api/admin/stations?kind=police").get_json()["count"] >= 1
    alist = client.get("/api/admin/ambulances").get_json()
    assert alist["count"] >= 1
    assert alist.get("managed_by") == "hospital"
    assert client.get("/api/admin/call-centers").get_json()["count"] >= 1
    client.post(f"/api/admin/stations/{sid}/toggle")


def test_command_center_map_shows_police_fire_stations(client):
    """Live map Police/Fire counts must match open stations (not only operator accounts)."""
    import app as app_module

    client.post(
        "/api/admin/stations",
        json={
            "kind": "police",
            "name": "Map Police",
            "city": "Mogadishu",
            "phone": "611000001",
            "latitude": 2.04,
            "longitude": 45.3,
            "operating_status": "open",
        },
    )
    client.post(
        "/api/admin/stations",
        json={
            "kind": "fire",
            "name": "Map Fire",
            "city": "Mogadishu",
            "phone": "611000002",
            "latitude": 2.05,
            "longitude": 45.31,
            "operating_status": "open",
        },
    )
    # Operator without GPS — must not inflate map count by itself
    client.post(
        "/api/admin/users/create",
        json={
            "name": "Map Officer",
            "email": "map.officer@example.com",
            "password": "Secret123!",
            "role": "police",
            "station_id": 1,
        },
    )
    payload = app_module._admin_command_payload()
    kinds = {}
    for m in payload.get("map_markers") or []:
        kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
    assert payload["police_online"] == 1
    assert payload["fire_online"] == 1
    assert payload["police_operators"] >= 1
    assert kinds.get("police") == 1
    assert kinds.get("fire") == 1
    assert payload["police_online"] == kinds.get("police")
    assert payload["fire_online"] == kinds.get("fire")


def test_hospital_ambulance_dispatch_essentials(client):
    rh = client.post(
        "/api/admin/hospitals",
        json={
            "name": "Dispatch Essentials Hosp",
            "region": "Banadir",
            "district": "Hodan",
            "city": "Mogadishu",
            "address": "Hodan",
            "phone": "614444444",
            "latitude": 2.04,
            "longitude": 45.33,
            "services": ["Emergency Care"],
            "owner_name": "Essentials Admin",
            "owner_email": "essentials.hosp@example.com",
            "owner_password": "Secret123!",
        },
    )
    owner_id = rh.get_json()["owner"]["id"]
    with client.session_transaction() as sess:
        sess["user_id"] = owner_id
        sess["role"] = "hospital"
        sess["name"] = "Essentials Admin"
        sess["email"] = "essentials.hosp@example.com"

    # available without driver phone → rejected
    bad = client.post(
        "/api/hospital/ambulances",
        json={"call_sign": "AMB-X", "status": "available"},
    )
    assert bad.status_code == 400

    ok = client.post(
        "/api/hospital/ambulances",
        json={
            "call_sign": "AMB-X",
            "status": "available",
            "driver_name": "Ali",
            "driver_phone": "615555555",
            "latitude": 2.05,
            "longitude": 45.32,
        },
    )
    assert ok.status_code == 201
    aid = ok.get_json()["ambulance"]["id"]
    loc = client.post(
        f"/api/hospital/ambulances/{aid}/location",
        json={"latitude": 2.051, "longitude": 45.321},
    )
    assert loc.status_code == 200
    assert loc.get_json()["ambulance"]["latitude"] == 2.051
    listed = client.get("/api/hospital/ambulances").get_json()
    assert listed["available_count"] == 1



def test_dispatch_workflow(client):
    rh = client.post(
        "/api/admin/hospitals",
        json={
            "name": "Dispatch Hosp",
            "region": "Banadir",
            "district": "Hodan",
            "city": "Mogadishu",
            "address": "Hodan",
            "phone": "613333333",
            "latitude": 2.04,
            "longitude": 45.33,
            "services": ["Emergency Care"],
            "owner_name": "Dispatch Admin",
            "owner_email": "dispatch.hosp@example.com",
            "owner_password": "Secret123!",
        },
    )
    hid = rh.get_json()["hospital"]["id"]
    import app as app_module

    edata = app_module.load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    edata["emergencies"].append(
        {
            "id": eid,
            "type": "medical",
            "status": "pending",
            "location": "Mogadishu",
            "caller_name": "Citizen",
            "phone": "610000000",
            "latitude": 2.04,
            "longitude": 45.33,
            "assigned_to": "hospital",
            "timestamp": "2026-07-20 10:00:00",
            "status_history": [],
            "escalation_queue": [],
            "escalation_index": 0,
        }
    )
    app_module.save_emergencies(edata)
    rd = client.post(
        "/api/admin/emergencies/dispatch",
        json={"id": eid, "assigned_to": "hospital", "assigned_hospital_id": hid, "notes": "test"},
    )
    assert rd.status_code == 200
    body = rd.get_json()
    assert body["success"] is True
    assert body["emergency"]["assigned_hospital_id"] == hid
    assert body["emergency"]["status"] == "pending_hospital"
    ru = client.post(
        "/api/admin/emergencies/update",
        json={"id": eid, "status": "in_progress", "assigned_to": "hospital"},
    )
    assert ru.status_code == 200
    rv = client.post(
        "/api/admin/emergencies/verify",
        json={"id": eid, "resolve": True, "notes": "ok"},
    )
    assert rv.status_code == 200
    assert rv.get_json()["emergency"]["status"] == "resolved"


def test_hospital_accept_assigns_and_releases_ambulance(client):
    """Accept with unit → busy; reached_victim → available again."""
    import app as app_module

    rh = client.post(
        "/api/admin/hospitals",
        json={
            "name": "Loop Hosp",
            "region": "Banadir",
            "district": "Hodan",
            "city": "Mogadishu",
            "address": "Hodan",
            "phone": "616666666",
            "latitude": 2.04,
            "longitude": 45.33,
            "services": ["Emergency Care"],
            "owner_name": "Loop Admin",
            "owner_email": "loop.hosp@example.com",
            "owner_password": "Secret123!",
        },
    )
    assert rh.status_code == 201
    body = rh.get_json()
    hid = body["hospital"]["id"]
    owner_id = body["owner"]["id"]

    with client.session_transaction() as sess:
        sess["user_id"] = owner_id
        sess["role"] = "hospital"
        sess["name"] = "Loop Admin"
        sess["email"] = "loop.hosp@example.com"

    ra = client.post(
        "/api/hospital/ambulances",
        json={
            "call_sign": "AMB-LOOP",
            "status": "available",
            "driver_name": "Driver Loop",
            "driver_phone": "617777777",
            "latitude": 2.041,
            "longitude": 45.331,
        },
    )
    assert ra.status_code == 201
    aid = ra.get_json()["ambulance"]["id"]

    # Seed emergency assigned to this hospital
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["role"] = "super_admin"
        sess["name"] = "Super Admin"
        sess["email"] = "super@example.com"

    edata = app_module.load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    edata["emergencies"].append(
        {
            "id": eid,
            "type": "medical",
            "status": "pending_hospital",
            "location": "Bakaro",
            "caller_name": "Citizen",
            "phone": "610000111",
            "latitude": 2.046,
            "longitude": 45.32,
            "assigned_to": "hospital",
            "assigned_hospital_id": hid,
            "timestamp": "2026-07-21 10:00:00",
            "status_history": [],
            "responder_status": {},
            "escalation_queue": [],
            "escalation_index": 0,
        }
    )
    app_module.save_emergencies(edata)

    with client.session_transaction() as sess:
        sess["user_id"] = owner_id
        sess["role"] = "hospital"
        sess["name"] = "Loop Admin"
        sess["email"] = "loop.hosp@example.com"

    acc = client.post(
        f"/api/hospital/request/{eid}/accept",
        json={"ambulance_unit_id": aid},
    )
    assert acc.status_code == 200, acc.get_data(as_text=True)
    assert acc.get_json()["assigned_ambulance_id"] == aid

    units = client.get("/api/hospital/ambulances").get_json()["ambulances"]
    unit = next(u for u in units if u["id"] == aid)
    assert unit["status"] == "busy"

    done = client.post(
        f"/api/emergencies/{eid}/responder",
        json={"action": "reached_victim"},
    )
    assert done.status_code == 200
    assert done.get_json()["status"] == "completed"

    units2 = client.get("/api/hospital/ambulances").get_json()["ambulances"]
    unit2 = next(u for u in units2 if u["id"] == aid)
    assert unit2["status"] == "available"
