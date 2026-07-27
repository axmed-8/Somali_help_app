"""
End-to-end QA audit: hit every page and major API as each role.
Run: python scripts/e2e_full_audit.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["TESTING"] = "1"
os.environ["GURMADNET_DB"] = "json"
os.environ["EMAIL_PROVIDER"] = "memory"
os.environ["AI_PROVIDER"] = "rule_based"
os.environ["ALLOW_TEST_EMAILS"] = "true"

import importlib
import app as ers
from email_service.factory import clear_email_provider_cache
from email_service.memory_provider import clear_outbox, OUTBOX
from ai_engine.factory import clear_provider_cache
from ai_engine import service as ai_service
from werkzeug.security import generate_password_hash

clear_email_provider_cache()
clear_provider_cache()
clear_outbox()
ai_service._engine_singleton = None
importlib.reload(ers)

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "database")
os.makedirs(db, exist_ok=True)
ers.DATABASE_DIR = db
ers.USERS_FILE = os.path.join(db, "users.json")
ers.EMERGENCIES_FILE = os.path.join(db, "emergencies.json")
ers.CONTENT_FILE = os.path.join(db, "system_content.json")
ers.SETTINGS_FILE = os.path.join(db, "settings.json")
ers.AUDIT_FILE = os.path.join(db, "audit_log.json")
ers.ANNOUNCEMENTS_FILE = os.path.join(db, "announcements.json")
ers.configure_hospital_db(db)
ers.seed_defaults()
ers.app.config["TESTING"] = True
ers.app.config["WTF_CSRF_ENABLED"] = False

udata = ers.load_users()
for name, email, pw, role, phone in [
    ("Ahmed", "ahmed@example.com", "123456", "citizen", "0611"),
    ("Amina", "amina@hospital.com", "123456", "hospital", "0622"),
    ("Hassan", "hassan@police.com", "123456", "police", "0633"),
    ("Muse", "muse@fire.com", "123456", "fire", "0644"),
    ("Admin", "admin@emergency.so", "admin123", "admin", "0610"),
    ("Operator", "operator@callcenter.so", "123456", "call_center", "+252612000999"),
]:
    if not any(u["email"] == email for u in udata["users"]):
        uid = udata["next_id"]
        udata["next_id"] += 1
        u = {
            "id": uid,
            "name": name,
            "email": email,
            "phone": phone,
            "password_hash": generate_password_hash(pw),
            "role": role,
            "status": "active",
            "email_verified": True,
            "created_at": ers.now_str(),
            "activity": [],
        }
        if role == "hospital":
            u["hospital_id"] = 1
        udata["users"].append(u)
for u in udata["users"]:
    u["email_verified"] = True
    if u.get("role") == "hospital" and not u.get("hospital_id"):
        u["hospital_id"] = 1
    if u.get("role") == "police" and not u.get("station_id"):
        u["station_id"] = 1
    if u.get("role") == "fire" and not u.get("station_id"):
        u["station_id"] = 2
ers.save_users(udata)

import hospital_logic as hl
import facility_registry as fr

hl.save_hospitals(
    {
        "hospitals": [
            {
                "id": 1,
                "name": "Audit Test Hospital",
                "city": "Mogadishu",
                "region": "Banadir",
                "district": "Hodan",
                "address": "Hodan",
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
                "owner_user_id": next(
                    (u["id"] for u in udata["users"] if u["email"] == "amina@hospital.com"),
                    None,
                ),
                "location_verified": True,
                "created_at": ers.now_str(),
                "updated_at": ers.now_str(),
            }
        ],
        "next_id": 2,
    },
    ers.save_json,
)
fr.save_stations(
    {
        "stations": [
            {
                "id": 1,
                "kind": "police",
                "name": "Audit Police Station",
                "city": "Mogadishu",
                "region": "Banadir",
                "district": "Hodan",
                "address": "Hodan",
                "latitude": 2.038,
                "longitude": 45.315,
                "phone": "0633333333",
                "operating_status": "open",
                "owner_user_id": next(
                    (u["id"] for u in udata["users"] if u["email"] == "hassan@police.com"),
                    None,
                ),
                "created_at": ers.now_str(),
                "updated_at": ers.now_str(),
            },
            {
                "id": 2,
                "kind": "fire",
                "name": "Audit Fire Station",
                "city": "Mogadishu",
                "region": "Banadir",
                "district": "Hodan",
                "address": "Hodan",
                "latitude": 2.052,
                "longitude": 45.328,
                "phone": "0644444444",
                "operating_status": "open",
                "owner_user_id": next(
                    (u["id"] for u in udata["users"] if u["email"] == "muse@fire.com"),
                    None,
                ),
                "created_at": ers.now_str(),
                "updated_at": ers.now_str(),
            },
        ],
        "next_id": 3,
    },
    ers.save_json,
)

client = ers.app.test_client()
passed = failed = 0
issues = []


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS - {name}")
    else:
        failed += 1
        issues.append(f"{name}: {detail}")
        print(f"FAIL - {name} :: {detail}")


def login(email, password):
    client.get("/logout", follow_redirects=True)
    return client.post("/login", data={"username": email, "password": password}, follow_redirects=True)


# ---- Public pages ----
for path in ("/login", "/signup", "/forgot-password", "/call-center/login"):
    r = client.get(path)
    check(f"GET {path}", r.status_code == 200, f"status={r.status_code}")

# Broken link sanity: pages should not 500
r = client.get("/reset-password/not-a-token", follow_redirects=True)
check("Invalid reset token handled", r.status_code == 200)

# ---- Signup + login (citizen-only) ----
import re
clear_outbox()
email = "e2e.audit@test.so"
r = client.post(
    "/signup",
    data={
        "first_name": "E2E",
        "middle_name": "",
        "last_name": "Audit",
        "gender": "male",
        "date_of_birth": "1991-07-07",
        "email": email,
        "phone": "0615555555",
        "address": "",
        "city": "",
        "emergency_contact_name": "E2E Contact",
        "emergency_contact_email": "e2e.contact@test.so",
        "emergency_contact_phone": "0616666666",
        "emergency_contact_relation": "Friend",
        "national_id": "",
        "blood_type": "",
        "medical_conditions": "",
        "allergies": "",
        "password": "123456",
        "confirm_password": "123456",
        "agree_terms": "1",
    },
    follow_redirects=True,
)
check("Signup", r.status_code == 200 and (b"account created" in r.data.lower() or b"verification" in r.data.lower()))
user, _ = ers.get_user_by_login(email)
check("Signup citizen", user and user.get("role") == "citizen")
m = re.search(r"verification code is:\s*(\d{6})", OUTBOX[-1]["text"], re.I) if OUTBOX else None
if m:
    client.post("/verify-email", data={"email": email, "otp": m.group(1)}, follow_redirects=True)
r = login(email, "123456")
check("Login after signup", b"invalid email or password" not in r.data.lower())
check("Dashboard after signup", client.get("/dashboard").status_code == 200)
client.get("/logout", follow_redirects=True)

# ---- Role pages ----
role_pages = {
    "ahmed@example.com": [
        ("/", 200),
        ("/dashboard", 200),
        ("/api/user/dashboard", 200),
        ("/api/user/profile", 200),
        ("/api/notifications", 200),
        ("/api/my_emergencies", 200),
        ("/api/announcements", 200),
    ],
    "amina@hospital.com": [
        ("/hospital", 200),
        ("/hospital/register", (200, 302)),  # 302 if already registered
        ("/api/get_emergencies", 200),
        ("/api/hospital/profile", 200),
        ("/api/hospitals", 200),
    ],
    "hassan@police.com": [("/police", 200), ("/api/get_emergencies", 200)],
    "muse@fire.com": [("/fire", 200), ("/api/get_emergencies", 200)],
    "admin@emergency.so": [
        ("/admin", 200),
        ("/api/admin/stats", 200),
        ("/api/admin/users", 200),
        ("/api/admin/settings", 200),
        ("/api/admin/content", 200),
        ("/api/admin/ai/stats", 200),
        ("/api/admin/call-center/stats", 200),
    ],
    "operator@callcenter.so": [
        ("/call-center", 200),
        ("/call-center/history", 200),
        ("/api/call-center/live", 200),
        ("/api/call-center/history", 200),
        ("/api/call-center/settings", 200),
    ],
}

for email_addr, paths in role_pages.items():
    pw = "admin123" if email_addr.startswith("admin") else "123456"
    login(email_addr, pw)
    for path, expect in paths:
        r = client.get(path)
        if isinstance(expect, tuple):
            ok = r.status_code in expect
        else:
            ok = r.status_code == expect
        body_ok = True
        if path in ("/", "/dashboard", "/hospital", "/police", "/fire", "/admin", "/call-center"):
            body_ok = b"Traceback" not in r.data and b"jinja2.exceptions" not in r.data
        check(f"{email_addr} GET {path}", ok and body_ok, f"status={r.status_code}")

# Cross-role forbidden
login("ahmed@example.com", "123456")
for path in ("/hospital", "/police", "/fire", "/admin", "/call-center", "/api/admin/stats", "/api/call-center/live"):
    r = client.get(path, follow_redirects=False)
    check(
        f"Citizen forbidden {path}",
        r.status_code in (302, 401, 403) or (r.is_json and r.get_json().get("success") is False),
        f"status={r.status_code}",
    )

# ---- SOS + hospital + chat + tracking ----
login("ahmed@example.com", "123456")
r = client.post(
    "/api/send_alert",
    json={
        "type": "medical",
        "latitude": 2.0469,
        "longitude": 45.3182,
        "location": "Hodan",
        "district": "Hodan",
        "name": "Ahmed",
        "phone": "061",
        "notes": "audit sos",
    },
)
data = r.get_json() or {}
check("SOS medical", r.status_code == 200 and data.get("success"), str(data))
eid = data.get("id")
if eid:
    r = client.post(f"/api/emergencies/{eid}/location", json={"latitude": 2.047, "longitude": 45.319})
    check("Location update", r.status_code == 200)
    r = client.get(f"/api/emergencies/{eid}/tracking")
    check("Tracking", r.status_code == 200)
    r = client.post(f"/api/messages/{eid}", json={"message": "help please"})
    check("Chat send", r.status_code == 200 and (r.get_json() or {}).get("success"))
    r = client.get(f"/api/messages/{eid}")
    check("Chat get", r.status_code == 200)

    login("amina@hospital.com", "123456")
    r = client.get("/api/get_emergencies")
    check("Hospital list emergencies", r.status_code == 200)
    # accept if endpoint exists
    r = client.post(f"/api/hospital/request/{eid}/accept", json={})
    check("Hospital accept", r.status_code == 200 and (r.get_json() or {}).get("success"), r.get_data(as_text=True)[:200])

# Fire + police SOS
login("ahmed@example.com", "123456")
for etype, role_email, note in (
    ("fire", "muse@fire.com", "smoke"),
    ("security", "hassan@police.com", "theft"),
):
    r = client.post(
        "/api/send_alert",
        json={"type": etype, "latitude": 2.05, "longitude": 45.32, "location": "Mog", "notes": note},
    )
    d = r.get_json() or {}
    check(f"SOS {etype}", r.status_code == 200 and d.get("success"), str(d))
    eid2 = d.get("id")
    login(role_email, "123456")
    if eid2:
        r = client.post("/api/update_status", json={"id": eid2, "status": "dispatched"})
        check(f"{etype} update status", r.status_code == 200, r.get_data(as_text=True)[:160])
        r = client.post("/api/update_status", json={"id": eid2, "status": "completed"})
        check(f"{etype} complete", r.status_code == 200, r.get_data(as_text=True)[:160])
    login("ahmed@example.com", "123456")

# Call center
login("ahmed@example.com", "123456")
r = client.post(
    "/api/call-center/initiate",
    json={"latitude": 2.0469, "longitude": 45.3182, "address": "Hodan", "name": "Ahmed", "phone": "061"},
)
cc = r.get_json() or {}
check("CC initiate", r.status_code == 200 and cc.get("success"), str(cc))
cid = cc.get("call_id")
login("operator@callcenter.so", "123456")
if cid:
    r = client.post(f"/api/call-center/calls/{cid}/answer", json={})
    check("CC answer", r.status_code == 200)
    r = client.post(f"/api/call-center/calls/{cid}/ai/analyze", json={})
    check("CC AI analyze", r.status_code == 200)
    r = client.post(
        f"/api/call-center/calls/{cid}/dispatch",
        json={"types": ["medical"], "notes": "e2e"},
    )
    check("CC dispatch", r.status_code == 200 and (r.get_json() or {}).get("success"), r.get_data(as_text=True)[:200])

# Profile XSS payload rejected
login("ahmed@example.com", "123456")
evil = "x' onerror='alert(1)"
r = client.put("/api/user/profile", json={"profile_photo": evil})
pj = r.get_json() or {}
check(
    "Profile photo XSS rejected",
    r.status_code == 400 and pj.get("success") is False,
    str(pj)[:200],
)

# Forgot password OTP
clear_outbox()
r = client.post("/forgot-password", data={"email": "ahmed@example.com"}, follow_redirects=True)
check("Forgot password OTP email", r.status_code == 200 and len(OUTBOX) >= 1)
m = re.search(r"reset code is:\s*(\d{6})", OUTBOX[-1]["text"], re.I)
check("OTP in email", bool(m), OUTBOX[-1]["text"][:120] if OUTBOX else "")
if m:
    r = client.post(
        "/forgot-password/verify",
        data={"email": "ahmed@example.com", "otp": m.group(1)},
        follow_redirects=False,
    )
    check("OTP verify", r.status_code in (302, 303) and "/reset-password/" in (r.headers.get("Location") or ""))

# Unauth API
client.get("/logout", follow_redirects=True)
r = client.get("/api/user/dashboard")
check("Unauth API JSON", r.status_code == 401 and r.is_json)

print("\n=== E2E AUDIT SUMMARY ===")
print(f"passed {passed} failed {failed}")
if issues:
    print("ISSUES:")
    for i in issues:
        print(" -", i)

shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if failed else 0)
