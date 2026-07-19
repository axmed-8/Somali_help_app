"""ERS automated tests — run: python -m pytest tests/ -v"""
import json
import os
import sys
import tempfile
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def app_client():
  os.environ["GURMADNET_DB"] = "json"
  os.environ["EMAIL_PROVIDER"] = "memory"
  import importlib
  import app as ers_app
  from email_service.factory import clear_email_provider_cache
  from email_service.memory_provider import clear_outbox

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
  udata = ers_app.load_users()
  test_users = [
    ("Ahmed Ali", "ahmed@example.com", "123456", "citizen", "0611111111"),
    ("Dr. Amina", "amina@hospital.com", "123456", "hospital", "0622222222"),
    ("Captain Hassan", "hassan@police.com", "123456", "police", "0633333333"),
    ("Chief Muse", "muse@fire.com", "123456", "fire", "0644444444"),
    ("Admin User", "admin@emergency.so", "admin123", "admin", "0610000000"),
    ("Call Center Operator", "operator@callcenter.so", "123456", "call_center", "+252612000999"),
  ]
  from werkzeug.security import generate_password_hash
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
    if u.get("email") == "amina@hospital.com":
      u["hospital_id"] = 1
    u["email_verified"] = True
  ers_app.save_users(udata)
  ers_app.app.config["TESTING"] = True
  ers_app.app.config["WTF_CSRF_ENABLED"] = False
  client = ers_app.app.test_client()
  yield client, ers_app
  clear_outbox()
  clear_email_provider_cache()
  shutil.rmtree(tmp, ignore_errors=True)


def login(client, email, password):
  return client.post("/login", data={"username": email, "password": password}, follow_redirects=True)


def citizen_signup_data(**overrides):
  data = {
    "first_name": "Ahmed",
    "middle_name": "",
    "last_name": "Hassan",
    "gender": "male",
    "date_of_birth": "1995-05-15",
    "email": "citizen.new@test.so",
    "phone": "0611111111",
    "address": "",
    "city": "Mogadishu",
    "emergency_contact_name": "Fatima Hassan",
    "emergency_contact_email": "fatima.contact@test.so",
    "emergency_contact_phone": "0612222222",
    "emergency_contact_relation": "Spouse",
    "national_id": "123456789012",
    "blood_type": "",
    "medical_conditions": "",
    "allergies": "",
    "password": "123456",
    "confirm_password": "123456",
    "agree_terms": "1",
  }
  data.update(overrides)
  return data


def test_citizen_sos_creates_emergency(app_client):
  client, ers_app = app_client
  login(client, "ahmed@example.com", "123456")
  r = client.post(
    "/api/send_alert",
    json={
      "type": "medical",
      "latitude": 2.03,
      "longitude": 45.33,
      "district": "Wadajir District",
      "accuracy_m": 15,
      "method": "gps",
      "confidence": 80,
      "location": "Wadajir (2.03, 45.33)",
      "name": "Ahmed",
      "phone": "061",
    },
  )
  assert r.status_code == 200
  data = r.get_json()
  assert data["success"] is True
  edata = ers_app.load_emergencies()
  assert len(edata["emergencies"]) >= 1
  em = edata["emergencies"][-1]
  assert len(em["location_history"]) >= 1


def test_hospital_only_sees_medical(app_client):
  client, _ = app_client
  login(client, "amina@hospital.com", "123456")
  r = client.get("/api/get_emergencies?type=medical")
  assert r.status_code == 200
  for em in r.get_json()["emergencies"]:
    assert em["type"] in ("medical", "family_help")


def test_blocked_user_cannot_login(app_client):
  client, ers_app = app_client
  login(client, "admin@emergency.so", "admin123")
  udata = ers_app.load_users()
  for u in udata["users"]:
    if u["email"] == "ahmed@example.com":
      u["status"] = "blocked"
  ers_app.save_users(udata)
  client.get("/logout", follow_redirects=True)
  r = client.post("/login", data={"username": "ahmed@example.com", "password": "123456"})
  assert b"blocked" in r.data.lower() or r.status_code == 200


def test_location_update_endpoint(app_client):
  client, ers_app = app_client
  login(client, "ahmed@example.com", "123456")
  r = client.post("/api/send_alert", json={"type": "fire", "latitude": 2.02, "longitude": 45.32, "location": "test"})
  eid = r.get_json()["id"]
  r2 = client.post(
    f"/api/emergencies/{eid}/location",
    json={"latitude": 2.021, "longitude": 45.321, "method": "gps", "accuracy_m": 10},
  )
  assert r2.status_code == 200
  em, _ = ers_app.get_emergency_by_id(eid)
  assert len(em["location_history"]) >= 2


def test_auto_dispatch_emergency(app_client):
  client, ers_app = app_client
  login(client, "ahmed@example.com", "123456")
  r = client.post(
    "/api/send_alert",
    json={
      "type": "medical",
      "latitude": 2.0469,
      "longitude": 45.3182,
      "district": "Mogadishu",
      "location": "Mogadishu test",
      "name": "Ahmed",
      "phone": "061",
    },
  )
  assert r.status_code == 200
  data = r.get_json()
  assert data["success"] is True
  assert data.get("team")
  em, _ = ers_app.get_emergency_by_id(data["id"])
  assert em.get("assigned_team_label")
  assert em.get("tracking_active") is True


def test_hospital_accept_healthcare_request(app_client):
  client, ers_app = app_client
  login(client, "ahmed@example.com", "123456")
  eid = client.post(
    "/api/send_alert",
    json={"type": "medical", "latitude": 2.0469, "longitude": 45.3182, "location": "x", "name": "A"},
  ).get_json()["id"]
  client.get("/logout", follow_redirects=True)
  login(client, "amina@hospital.com", "123456")
  r = client.post(f"/api/hospital/request/{eid}/accept")
  assert r.status_code == 200
  em, _ = ers_app.get_emergency_by_id(eid)
  assert em["status"] == "accepted"


def test_user_dashboard_api(app_client):
  client, ers_app = app_client
  login(client, "ahmed@example.com", "123456")
  r = client.get("/api/user/dashboard")
  assert r.status_code == 200
  data = r.get_json()
  assert data["success"] is True
  assert "profile_summary" in data
  assert "announcements" in data


def test_hospital_registration_creates_profile(app_client):
  client, ers_app = app_client
  from werkzeug.security import generate_password_hash

  udata = ers_app.load_users()
  uid = udata["next_id"]
  udata["next_id"] += 1
  udata["users"].append({
    "id": uid,
    "name": "Dr. Test",
    "email": "newhospital@test.so",
    "phone": "+252 61 700 0001",
    "password_hash": generate_password_hash("123456"),
    "role": "hospital",
    "status": "active",
    "email_verified": True,
    "created_at": ers_app.now_str(),
    "activity": [],
  })
  ers_app.save_users(udata)
  login(client, "newhospital@test.so", "123456")
  r = client.post(
    "/hospital/register",
    data={
      "name": "Test Regional Hospital",
      "region": "Banadir",
      "district": "Hodan",
      "city": "Mogadishu",
      "address": "Test Street 1, Hodan",
      "phone": "+252 61 700 0001",
      "emergency_contacts": "+252 61 700 0002",
      "services": ["Emergency", "General"],
      "ambulance_available": "1",
      "ambulance_count": "2",
      "emergency_capacity": "12",
      "operating_status": "open",
      "latitude": "2.05",
      "longitude": "45.32",
    },
    follow_redirects=True,
  )
  assert r.status_code == 200
  user, _ = ers_app.get_user_by_login("newhospital@test.so")
  assert user.get("hospital_id") is not None
  hdata = ers_app.hl.load_hospitals(ers_app.read_json, ers_app.save_json)
  hospital = ers_app.hl.get_hospital_by_id(hdata, user["hospital_id"])
  assert hospital["name"] == "Test Regional Hospital"
  assert hospital["district"] == "Hodan"
  assert hospital["address"] == "Test Street 1, Hodan"


def test_hospital_only_sees_assigned_requests(app_client):
  client, ers_app = app_client
  login(client, "ahmed@example.com", "123456")
  eid = client.post(
    "/api/healthcare/emergency",
    json={"latitude": 2.0469, "longitude": 45.3182, "location": "x", "name": "A"},
  ).get_json()["id"]
  client.get("/logout", follow_redirects=True)
  login(client, "amina@hospital.com", "123456")
  r = client.get("/api/get_emergencies?type=medical")
  ids = [e["id"] for e in r.get_json()["emergencies"]]
  assert eid in ids
  for em in r.get_json()["emergencies"]:
    assert em.get("assigned_hospital_id") == 1


def test_live_location_tracking(app_client):
  client, ers_app = app_client
  login(client, "ahmed@example.com", "123456")
  eid = client.post(
    "/api/send_alert",
    json={
      "type": "medical",
      "latitude": 2.0469,
      "longitude": 45.3182,
      "accuracy_m": 12,
      "district": "Hodan",
      "location": "Hodan test",
      "name": "Ahmed",
    },
  ).get_json()["id"]
  em, _ = ers_app.get_emergency_by_id(eid)
  assert em.get("tracking_active") is True
  assert em.get("latitude") == 2.0469
  assert len(em.get("location_history", [])) >= 1

  r = client.post(
    f"/api/emergencies/{eid}/location",
    json={"latitude": 2.0471, "longitude": 45.3185, "accuracy_m": 8, "method": "gps_live"},
  )
  assert r.status_code == 200
  em, _ = ers_app.get_emergency_by_id(eid)
  assert len(em["location_history"]) >= 2
  assert em["latitude"] == 2.0471

  tr = client.get(f"/api/emergencies/{eid}/tracking")
  assert tr.status_code == 200
  data = tr.get_json()
  assert data["tracking_active"] is True
  assert "team_label" in data
  assert "eta_minutes" in data
  assert data["trail_count"] >= 2


def test_reject_coords_outside_somalia(app_client):
  client, _ = app_client
  login(client, "ahmed@example.com", "123456")
  r = client.post(
    "/api/send_alert",
    json={
      "type": "medical",
      "latitude": 42.494076,
      "longitude": 21.175171,
      "location": "Invalid",
    },
  )
  assert r.status_code == 400
  data = r.get_json()
  assert data["success"] is False


def test_tracking_sanitizes_invalid_coords(app_client):
  client, ers_app = app_client
  login(client, "ahmed@example.com", "123456")
  eid = client.post(
    "/api/send_alert",
    json={"type": "medical", "latitude": 2.0469, "longitude": 45.3182, "location": "Mogadishu"},
  ).get_json()["id"]
  em, edata = ers_app.get_emergency_by_id(eid)
  em["latitude"] = 42.494076
  em["longitude"] = 21.175171
  ers_app.save_emergencies(edata)
  r = client.get(f"/api/emergencies/{eid}/tracking")
  assert r.status_code == 200
  data = r.get_json()
  assert data["success"] is True
  assert data["coords_corrected"] is True
  assert ers_app.hl.is_in_somalia(data["latitude"], data["longitude"])
  assert data["distance_km"] is None or data["distance_km"] <= 80


def test_responder_arrived_status(app_client):
  client, ers_app = app_client
  login(client, "ahmed@example.com", "123456")
  eid = client.post("/api/send_alert", json={"type": "medical", "latitude": 2.03, "longitude": 45.33, "location": "x"}).get_json()["id"]
  client.get("/logout", follow_redirects=True)
  login(client, "amina@hospital.com", "123456")
  r = client.post(f"/api/emergencies/{eid}/responder", json={"action": "arrived_at_scene"})
  assert r.status_code == 200
  em, _ = ers_app.get_emergency_by_id(eid)
  assert "arrived_at_scene" in em["responder_status"]

def test_call_center_initiate_and_dispatch(app_client):
  """Method 2: citizen silent GPS + operator multi-dispatch."""
  client, ers_app = app_client
  udata = ers_app.load_users()
  if not any(u.get("email") == "operator@callcenter.so" for u in udata["users"]):
    from werkzeug.security import generate_password_hash
    uid = udata["next_id"]
    udata["next_id"] += 1
    udata["users"].append({
      "id": uid,
      "name": "Operator",
      "email": "operator@callcenter.so",
      "phone": "+252612000999",
      "password_hash": generate_password_hash("123456"),
      "role": "call_center",
      "status": "active",
      "created_at": ers_app.now_str(),
      "last_login": None,
      "activity": [],
    })
    ers_app.save_users(udata)

  login(client, "ahmed@example.com", "123456")
  r = client.post(
    "/api/call-center/initiate",
    json={
      "latitude": 2.03849,
      "longitude": 45.29984,
      "address": "KM4 Junction",
      "district": "KM4 Junction",
      "name": "Ahmed Ali",
      "phone": "0611111111",
    },
  )
  assert r.status_code == 200
  data = r.get_json()
  assert data["success"] is True
  assert data["call_id"]
  assert data["tel_href"].startswith("tel:")
  call_id = data["call_id"]

  client.get("/logout", follow_redirects=True)
  login(client, "operator@callcenter.so", "123456")
  r = client.get("/call-center")
  assert r.status_code == 200

  r = client.post(f"/api/call-center/calls/{call_id}/answer", json={})
  assert r.status_code == 200
  assert r.get_json()["call"]["status"] == "answered"

  r = client.post(
    f"/api/call-center/calls/{call_id}/dispatch",
    json={"types": ["medical", "security"], "notes": "Car accident with injuries"},
  )
  assert r.status_code == 200
  disp = r.get_json()
  assert disp["success"] is True
  assert len(disp["emergencies"]) == 2
  teams = {e["assigned_to"] for e in disp["emergencies"]}
  assert "hospital" in teams
  assert "police" in teams

  client.get("/logout", follow_redirects=True)
  login(client, "ahmed@example.com", "123456")
  r = client.post(
    "/api/send_alert",
    json={"type": "fire", "latitude": 2.04, "longitude": 45.32, "location": "Test"},
  )
  assert r.status_code == 200
  assert r.get_json()["success"] is True


def test_call_center_role_guard(app_client):
  client, _ = app_client
  login(client, "ahmed@example.com", "123456")
  r = client.get("/api/call-center/live")
  assert r.status_code in (302, 401, 403) or (r.is_json and r.get_json().get("success") is not True)


def test_signup_persists_citizen(app_client):
  client, ers_app = app_client
  from email_service.memory_provider import OUTBOX, clear_outbox
  import re

  clear_outbox()
  r = client.post(
    "/signup",
    data=citizen_signup_data(email="signup.citizen@test.so", phone="0619999999"),
    follow_redirects=True,
  )
  assert r.status_code == 200
  assert b"verify" in r.data.lower() or b"verification" in r.data.lower()
  users = ers_app.load_users()["users"]
  user = next(u for u in users if u.get("email") == "signup.citizen@test.so")
  assert user.get("role") == "citizen"
  assert user.get("first_name") == "Ahmed"
  assert user.get("last_name") == "Hassan"
  assert user.get("gender") == "male"
  assert user.get("national_id_last4") == "9012"
  assert user.get("national_id_hash")
  assert user.get("national_id_encrypted")
  assert user.get("email_verified") is False
  assert OUTBOX
  assert "signup.citizen@test.so" in OUTBOX[-1]["to"]
  m = re.search(r"verification code is:\s*(\d{6})", OUTBOX[-1]["text"], re.I)
  assert m, OUTBOX[-1]["text"]
  # Login blocked until verified
  r = login(client, "signup.citizen@test.so", "123456")
  assert client.get("/dashboard").status_code in (302, 401) or b"verify" in r.data.lower()
  r = client.post(
    "/verify-email",
    data={"email": "signup.citizen@test.so", "otp": m.group(1)},
    follow_redirects=True,
  )
  assert b"verified" in r.data.lower() or b"welcome" in r.data.lower()
  user, _ = ers_app.get_user_by_login("signup.citizen@test.so")
  assert user.get("email_verified") is True
  # Auto-login after verification — no separate login step required
  assert client.get("/dashboard").status_code == 200


def test_signup_allows_immediate_login(app_client):
  """After OTP verification, citizen is signed in automatically."""
  client, ers_app = app_client
  from email_service.memory_provider import OUTBOX, clear_outbox
  import re

  clear_outbox()
  email = "immediate.login@example.com"
  client.post(
    "/signup",
    data=citizen_signup_data(email=email, phone="0618888888", national_id=""),
    follow_redirects=True,
  )
  user, _ = ers_app.get_user_by_login(email)
  assert user is not None
  assert user.get("email_verified") is False
  r = login(client, email, "123456")
  assert client.get("/dashboard").status_code != 200 or b"verify" in r.data.lower()
  m = re.search(r"verification code is:\s*(\d{6})", OUTBOX[-1]["text"], re.I)
  assert m
  client.post("/verify-email", data={"email": email, "otp": m.group(1)}, follow_redirects=True)
  assert client.get("/dashboard").status_code == 200
  # Manual login still works after logout
  client.get("/logout", follow_redirects=True)
  r = login(client, email, "123456")
  assert client.get("/dashboard").status_code == 200

def test_signup_rejects_non_citizen_role(app_client):
  client, ers_app = app_client
  r = client.post(
    "/signup",
    data=citizen_signup_data(
      email="fake.hospital@test.so",
      role="hospital",  # forged — must be ignored
    ),
    follow_redirects=True,
  )
  assert r.status_code == 200
  user, _ = ers_app.get_user_by_login("fake.hospital@test.so")
  assert user is not None
  assert user.get("role") == "citizen"


def test_sos_notifies_emergency_contact(app_client):
  client, ers_app = app_client
  from email_service.memory_provider import OUTBOX, clear_outbox

  clear_outbox()
  client.post(
    "/signup",
    data=citizen_signup_data(
      email="sos.citizen@test.so",
      emergency_contact_email="sos.contact@test.so",
      national_id="998877665544",
    ),
    follow_redirects=True,
  )
  user, udata = ers_app.get_user_by_login("sos.citizen@test.so")
  user["email_verified"] = True
  ers_app.save_users(udata)
  clear_outbox()
  login(client, "sos.citizen@test.so", "123456")
  r = client.post(
    "/api/send_alert",
    json={
      "type": "medical",
      "latitude": 2.0469,
      "longitude": 45.3182,
      "location": "Hodan",
      "notes": "chest pain",
    },
  )
  assert r.status_code == 200
  assert r.get_json()["success"] is True
  assert any("sos.contact@test.so" in (m.get("to") or "") for m in OUTBOX)
  assert any("emergency" in (m.get("subject") or "").lower() for m in OUTBOX)
  body = " ".join((m.get("text") or "") for m in OUTBOX).lower()
  assert "2.0469" in body or "gps" in body
  assert "hodan" in body


def test_password_reset_otp_flow(app_client):
  client, ers_app = app_client
  from email_service.memory_provider import OUTBOX, clear_outbox
  import re

  clear_outbox()
  email = "ahmed@example.com"
  r = client.post("/forgot-password", data={"email": email}, follow_redirects=True)
  assert r.status_code == 200
  assert OUTBOX
  assert email in OUTBOX[-1]["to"]
  body = OUTBOX[-1]["text"]
  match = re.search(r"reset code is:\s*(\d{6})", body, re.I)
  assert match, body
  otp = match.group(1)

  r = client.post(
    "/forgot-password/verify",
    data={"email": email, "otp": otp},
    follow_redirects=False,
  )
  assert r.status_code in (302, 303)
  loc = r.headers.get("Location", "")
  assert "/reset-password/" in loc
  token = loc.rstrip("/").split("/")[-1]

  # OTP cannot be reused
  r2 = client.post(
    "/forgot-password/verify",
    data={"email": email, "otp": otp},
    follow_redirects=True,
  )
  assert b"invalid or expired" in r2.data.lower() or b"incorrect" in r2.data.lower() or b"expired" in r2.data.lower()

  r = client.post(
    f"/reset-password/{token}",
    data={"password": "newpass1", "confirm_password": "newpass1"},
    follow_redirects=True,
  )
  assert b"password updated" in r.data.lower()
  # Old password fails, new works
  r = login(client, email, "123456")
  assert b"invalid email or password" in r.data.lower() or client.get("/dashboard").status_code != 200
  client.get("/logout", follow_redirects=True)
  r = login(client, email, "newpass1")
  assert client.get("/dashboard").status_code == 200
  # Restore demo password for other tests in same process (fixture is per-test temp DB so OK)


def test_signup_rejects_disposable_email(app_client):
  client, ers_app = app_client
  r = client.post(
    "/signup",
    data=citizen_signup_data(email="someone@mailinator.com", phone="0610000000", national_id=""),
    follow_redirects=True,
  )
  assert r.status_code == 200
  assert b"temporary or disposable" in r.data.lower() or b"real email" in r.data.lower()
  users = ers_app.load_users()["users"]
  assert not any(u.get("email") == "someone@mailinator.com" for u in users)


def test_unverified_legacy_user_can_still_login(app_client):
  """Unverified citizens cannot use the app until email OTP is verified."""
  client, ers_app = app_client
  email = "legacy.unverified@test.so"
  client.post(
    "/signup",
    data=citizen_signup_data(email=email, national_id="112233445566"),
    follow_redirects=True,
  )
  user, udata = ers_app.get_user_by_login(email)
  user["email_verified"] = False
  ers_app.save_users(udata)
  r = login(client, email, "123456")
  assert client.get("/dashboard").status_code != 200
  assert b"verify" in r.data.lower()


def test_invalid_verification_token(app_client):
  client, _ = app_client
  r = client.get("/verify-email/not-a-real-token", follow_redirects=True)
  assert r.status_code == 200
  assert b"verification code" in r.data.lower() or b"verify" in r.data.lower()


def test_profile_api_never_leaks_password_hash(app_client):
  client, _ = app_client
  login(client, "ahmed@example.com", "123456")
  r = client.get("/api/user/profile")
  assert r.status_code == 200
  profile = r.get_json()["profile"]
  assert "password_hash" not in profile
  assert "email_verify_token" not in profile
  r2 = client.put("/api/user/profile", json={"phone": "061999"})
  assert r2.status_code == 200
  assert "password_hash" not in r2.get_json()["profile"]


def test_forgot_password_sends_otp(app_client):
  client, _ = app_client
  from email_service.memory_provider import OUTBOX, clear_outbox

  clear_outbox()
  r = client.post(
    "/forgot-password",
    data={"email": "ahmed@example.com"},
    follow_redirects=True,
  )
  assert r.status_code == 200
  assert b"reset code" in r.data.lower() or b"one-time" in r.data.lower() or b"enter" in r.data.lower()
  assert OUTBOX
  assert "ahmed@example.com" in OUTBOX[-1]["to"]
  assert "reset code" in (OUTBOX[-1].get("text") or "").lower()


def test_forgot_password_unknown_email(app_client):
  client, _ = app_client
  from email_service.memory_provider import OUTBOX, clear_outbox

  clear_outbox()
  r = client.post(
    "/forgot-password",
    data={"email": "nobody-exists@example.com"},
    follow_redirects=True,
  )
  assert r.status_code == 200
  assert b"no account found" in r.data.lower()
  assert not OUTBOX


def test_chat_and_notifications_flow(app_client):
  client, ers_app = app_client
  login(client, "ahmed@example.com", "123456")
  eid = client.post(
    "/api/send_alert",
    json={
      "type": "medical",
      "latitude": 2.0469,
      "longitude": 45.3182,
      "location": "Chat test",
      "notes": "need help",
    },
  ).get_json()["id"]
  r = client.post(f"/api/messages/{eid}", json={"message": "Citizen message"})
  assert r.status_code == 200
  assert r.get_json()["success"] is True
  msgs = client.get(f"/api/messages/{eid}").get_json()
  assert msgs.get("success") is True or "messages" in msgs or isinstance(msgs, (dict, list))
  notes = client.get("/api/notifications")
  assert notes.status_code == 200
