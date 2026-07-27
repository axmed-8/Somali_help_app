"""Call Center location + nearest hospital/police/fire ranking."""
import os
import shutil
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def login(client, email, password):
    return client.post("/login", data={"username": email, "password": password}, follow_redirects=True)


@pytest.fixture
def app_client():
    os.environ["GURMADNET_DB"] = "json"
    os.environ["EMAIL_PROVIDER"] = "memory"
    os.environ["TESTING"] = "1"
    import importlib
    import app as ers_app
    from email_service.factory import clear_email_provider_cache
    from email_service.memory_provider import clear_outbox
    from werkzeug.security import generate_password_hash

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

    import hospital_logic as hl
    import facility_registry as fr

    hdata = hl.load_hospitals(ers_app.read_json, ers_app.save_json)
    hdata["hospitals"] = [{
        "id": 1,
        "name": "Near Hospital",
        "city": "Mogadishu",
        "region": "Banadir",
        "district": "Hodan",
        "address": "Near",
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
        "contact_email": "near@hospital.com",
        "owner_user_id": None,
        "location_verified": True,
        "created_at": ers_app.now_str(),
        "updated_at": ers_app.now_str(),
    }]
    hdata["next_id"] = 2
    hl.save_hospitals(hdata, ers_app.save_json)

    fr.create_station({
        "kind": "police",
        "name": "Far Police",
        "city": "Mogadishu",
        "phone": "061000001",
        "latitude": 2.15,
        "longitude": 45.45,
        "operating_status": "open",
    }, ers_app.read_json, ers_app.save_json)
    near_police = fr.create_station({
        "kind": "police",
        "name": "Near Police",
        "city": "Mogadishu",
        "phone": "061000002",
        "latitude": 2.039,
        "longitude": 45.301,
        "operating_status": "open",
    }, ers_app.read_json, ers_app.save_json)
    fr.create_station({
        "kind": "fire",
        "name": "Far Fire",
        "city": "Mogadishu",
        "phone": "064000001",
        "latitude": 2.20,
        "longitude": 45.50,
        "operating_status": "open",
    }, ers_app.read_json, ers_app.save_json)
    near_fire = fr.create_station({
        "kind": "fire",
        "name": "Near Fire",
        "city": "Mogadishu",
        "phone": "064000002",
        "latitude": 2.040,
        "longitude": 45.302,
        "operating_status": "open",
    }, ers_app.read_json, ers_app.save_json)

    udata = ers_app.load_users()
    test_users = [
        ("Ahmed Ali", "ahmed@example.com", "123456", "citizen", "0611111111"),
        ("Call Center Operator", "operator@callcenter.so", "123456", "call_center", "+252612000999"),
    ]
    for name, email, password, role, phone in test_users:
        uid = udata["next_id"]
        udata["next_id"] += 1
        udata["users"].append({
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
        })
    for u in udata["users"]:
        u["email_verified"] = True
    ers_app.save_users(udata)

    ers_app.app.config["TESTING"] = True
    ers_app.app.config["WTF_CSRF_ENABLED"] = False
    client = ers_app.app.test_client()
    yield client, ers_app, {"near_police_id": near_police["id"], "near_fire_id": near_fire["id"]}
    clear_outbox()
    clear_email_provider_cache()
    shutil.rmtree(tmp, ignore_errors=True)


def test_initiate_live_includes_gps(app_client):
    client, ers_app, _ids = app_client
    login(client, "ahmed@example.com", "123456")
    r = client.post(
        "/api/call-center/initiate",
        json={
            "latitude": 2.03849,
            "longitude": 45.29984,
            "address": "KM4 Junction",
            "name": "Ahmed Ali",
            "phone": "0611111111",
        },
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    call_id = body["call_id"]
    assert body["call"]["latitude"] == pytest.approx(2.03849)
    assert body["call"]["longitude"] == pytest.approx(45.29984)

    client.get("/logout", follow_redirects=True)
    login(client, "operator@callcenter.so", "123456")
    live = client.get("/api/call-center/live").get_json()
    assert live["success"] is True
    match = next(c for c in live["calls"] if c["id"] == call_id)
    assert match["latitude"] == pytest.approx(2.03849)
    assert match["longitude"] == pytest.approx(45.29984)
    assert "KM4" in (match.get("address") or "")


def test_nearest_picks_closest_police_and_fire(app_client):
    client, ers_app, ids = app_client
    login(client, "ahmed@example.com", "123456")
    r = client.post(
        "/api/call-center/initiate",
        json={
            "latitude": 2.03849,
            "longitude": 45.29984,
            "address": "KM4 Junction",
            "name": "Ahmed Ali",
            "phone": "0611111111",
        },
    )
    assert r.status_code == 200
    call_id = r.get_json()["call_id"]
    client.get("/logout", follow_redirects=True)
    login(client, "operator@callcenter.so", "123456")

    nearest = client.get(f"/api/call-center/calls/{call_id}/nearest").get_json()
    assert nearest["success"] is True
    n = nearest["nearest"]
    assert n["hospital"] is not None
    assert n["police"]["name"] == "Near Police"
    assert n["police"]["id"] == ids["near_police_id"]
    assert n["fire"]["name"] == "Near Fire"
    assert n["fire"]["id"] == ids["near_fire_id"]


def test_location_update_then_dispatch_uses_new_gps(app_client):
    client, ers_app, _ids = app_client
    login(client, "ahmed@example.com", "123456")
    r = client.post(
        "/api/call-center/initiate",
        json={
            "latitude": 2.03849,
            "longitude": 45.29984,
            "address": "KM4 Junction",
            "name": "Ahmed Ali",
            "phone": "0611111111",
        },
    )
    assert r.status_code == 200
    call_id = r.get_json()["call_id"]
    client.get("/logout", follow_redirects=True)
    login(client, "operator@callcenter.so", "123456")

    client.post(f"/api/call-center/calls/{call_id}/answer", json={})

    new_lat, new_lng = 2.05000, 45.31000
    upd = client.post(
        f"/api/call-center/calls/{call_id}/location",
        json={
            "latitude": new_lat,
            "longitude": new_lng,
            "address": "Updated stay point — Hodan",
        },
    )
    assert upd.status_code == 200
    body = upd.get_json()
    assert body["success"] is True
    assert body["call"]["latitude"] == pytest.approx(new_lat)
    assert body["call"]["longitude"] == pytest.approx(new_lng)
    assert "Updated stay" in body["call"]["address"]
    assert body["call"].get("location_updated_at")
    assert body["nearest"].get("hospital") is not None

    disp = client.post(
        f"/api/call-center/calls/{call_id}/dispatch",
        json={"types": ["medical", "security", "fire"], "notes": "Caller will wait at new pin"},
    )
    assert disp.status_code == 200
    assert disp.get_json()["success"] is True

    edata = ers_app.load_emergencies()
    matched = [em for em in edata["emergencies"] if em.get("call_id") == call_id]
    assert matched
    for em in matched:
        assert em["latitude"] == pytest.approx(new_lat)
        assert em["longitude"] == pytest.approx(new_lng)
        assert "Updated stay" in (em.get("location") or "")


def test_alert_friin_creates_call_center_case(app_client):
    client, ers_app, _ids = app_client
    login(client, "ahmed@example.com", "123456")
    call_id = client.post(
        "/api/call-center/initiate",
        json={
            "latitude": 2.03849,
            "longitude": 45.29984,
            "address": "KM4",
            "name": "Ahmed",
            "phone": "0611111111",
        },
    ).get_json()["call_id"]
    client.get("/logout", follow_redirects=True)
    login(client, "operator@callcenter.so", "123456")
    client.post(f"/api/call-center/calls/{call_id}/answer", json={})
    r = client.post(
        f"/api/call-center/calls/{call_id}/alert",
        json={"target": "hospital", "notes": "Friin nearest hospital"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert body["target"] == "hospital"
    assert body["emergency"]["id"]
    assert body["emergency"]["latitude"] == pytest.approx(2.03849)
    assert body["emergency"]["longitude"] == pytest.approx(45.29984)
    edata = ers_app.load_emergencies()
    em = next(e for e in edata["emergencies"] if e["id"] == body["emergency"]["id"])
    assert em.get("source") == "call_center"
    assert em.get("assigned_to") == "hospital"
    assert em.get("latitude") == pytest.approx(2.03849)


def test_alert_uses_updated_location_and_preferred_station(app_client):
    client, ers_app, ids = app_client
    login(client, "ahmed@example.com", "123456")
    call_id = client.post(
        "/api/call-center/initiate",
        json={
            "latitude": 2.03849,
            "longitude": 45.29984,
            "address": "KM4",
            "name": "Ahmed",
            "phone": "0611111111",
        },
    ).get_json()["call_id"]
    client.get("/logout", follow_redirects=True)
    login(client, "operator@callcenter.so", "123456")
    client.post(f"/api/call-center/calls/{call_id}/answer", json={})
    new_lat, new_lng = 2.05011, 45.31022
    client.post(
        f"/api/call-center/calls/{call_id}/location",
        json={"latitude": new_lat, "longitude": new_lng, "address": "Corrected pin"},
    )
    r = client.post(
        f"/api/call-center/calls/{call_id}/alert",
        json={
            "target": "police",
            "preferred_id": ids["near_police_id"],
            "notes": "Friin with corrected GPS",
        },
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert body["emergency"]["latitude"] == pytest.approx(new_lat)
    assert body["emergency"]["longitude"] == pytest.approx(new_lng)
    assert body["emergency"]["assigned_station_id"] == ids["near_police_id"]
    edata = ers_app.load_emergencies()
    em = next(e for e in edata["emergencies"] if e["id"] == body["emergency"]["id"])
    assert "Corrected pin" in (em.get("location") or "")
    assert em.get("assigned_station_id") == ids["near_police_id"]


def test_location_update_blocked_when_closed(app_client):
    client, ers_app, _ids = app_client
    login(client, "ahmed@example.com", "123456")
    r = client.post(
        "/api/call-center/initiate",
        json={
            "latitude": 2.03849,
            "longitude": 45.29984,
            "address": "KM4",
            "name": "Ahmed",
            "phone": "0611111111",
        },
    )
    assert r.status_code == 200
    call_id = r.get_json()["call_id"]
    client.get("/logout", follow_redirects=True)
    login(client, "operator@callcenter.so", "123456")
    client.post(f"/api/call-center/calls/{call_id}/answer", json={})
    client.post(f"/api/call-center/calls/{call_id}/complete", json={})
    r = client.post(
        f"/api/call-center/calls/{call_id}/location",
        json={"latitude": 2.05, "longitude": 45.31, "address": "Nope"},
    )
    assert r.status_code == 400
    assert r.get_json()["success"] is False
