"""Integration smoke verification for GurmadNet major features (JSON mode)."""
import importlib
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["GURMADNET_DB"] = "json"
os.environ["AI_PROVIDER"] = "rule_based"
os.environ["EMAIL_PROVIDER"] = "memory"

from werkzeug.security import generate_password_hash

from ai_engine import service as ai_service
from ai_engine.factory import clear_provider_cache

clear_provider_cache()
ai_service._engine_singleton = None

import app as ers

importlib.reload(ers)

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "database")
os.makedirs(db)
ers.DATABASE_DIR = db
ers.USERS_FILE = os.path.join(db, "users.json")
ers.EMERGENCIES_FILE = os.path.join(db, "emergencies.json")
ers.CONTENT_FILE = os.path.join(db, "system_content.json")
ers.SETTINGS_FILE = os.path.join(db, "settings.json")
ers.AUDIT_FILE = os.path.join(db, "audit_log.json")
ers.ANNOUNCEMENTS_FILE = os.path.join(db, "announcements.json")
ers.configure_hospital_db(db)
ers.seed_defaults()

udata = ers.load_users()
for name, email, pw, role, phone in [
    ("Ahmed", "ahmed@example.com", "123456", "citizen", "0611"),
    ("Amina", "amina@hospital.com", "123456", "hospital", "0622"),
    ("Hassan", "hassan@police.com", "123456", "police", "0633"),
    ("Muse", "muse@fire.com", "123456", "fire", "0644"),
    ("Admin", "admin@emergency.so", "admin123", "admin", "0610"),
    ("Operator", "operator@callcenter.so", "123456", "call_center", "+252612000999"),
]:
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
        "last_login": None,
        "activity": [],
    }
    if role == "hospital":
        u["hospital_id"] = 1
    udata["users"].append(u)
ers.save_users(udata)
ai_service._engine_singleton = None

ers.app.config["TESTING"] = True
ers.app.config["WTF_CSRF_ENABLED"] = False
c = ers.app.test_client()
results = []


def check(label, cond, detail=""):
    ok = bool(cond)
    results.append((label, ok, detail))
    print(("PASS" if ok else "FAIL"), "-", label, detail)


# Auth pages
for path in ("/login", "/signup", "/forgot-password"):
    r = c.get(path)
    check(f"GET {path}", r.status_code == 200, str(r.status_code))

# Signup + immediate login (no email verification gate)
from email_service.memory_provider import OUTBOX, clear_outbox
import re

clear_outbox()
r = c.post(
    "/signup",
    data={
        "name": "New User",
        "email": "newuser@test.so",
        "password": "123456",
        "confirm_password": "123456",
        "phone": "0699",
        "role": "citizen",
    },
    follow_redirects=True,
)
check("Signup HTTP", r.status_code == 200, str(r.status_code))
udata = ers.load_users()
nu = next((u for u in udata["users"] if u.get("email") == "newuser@test.so"), None)
check("Signup persisted", nu and nu.get("email_verified") is True)
r = c.post("/login", data={"username": "newuser@test.so", "password": "123456"}, follow_redirects=True)
check("Login after signup", r.status_code == 200 and b"verify your email" not in r.data.lower())
c.get("/logout", follow_redirects=True)

# OTP password reset
clear_outbox()
r = c.post("/forgot-password", data={"email": "ahmed@example.com"}, follow_redirects=True)
check("Forgot password OTP page", r.status_code == 200)
check("OTP email sent", bool(OUTBOX))
m = re.search(r"reset code is:\s*(\d{6})", OUTBOX[-1]["text"], re.I)
otp = m.group(1) if m else ""
check("OTP in email", bool(otp), OUTBOX[-1]["text"][:120] if OUTBOX else "")
r = c.post(
    "/forgot-password/verify",
    data={"email": "ahmed@example.com", "otp": otp},
    follow_redirects=False,
)
check("OTP verify redirect", r.status_code in (302, 303), str(r.status_code))
c.get("/logout", follow_redirects=True)

# Citizen login + pages
r = c.post(
    "/login",
    data={"username": "ahmed@example.com", "password": "123456"},
    follow_redirects=True,
)
check("Login citizen", r.status_code == 200)
for path in ("/", "/dashboard"):
    r = c.get(path)
    check(f"Citizen page {path}", r.status_code == 200, str(r.status_code))

# SOS
r = c.post(
    "/api/send_alert",
    json={
        "type": "medical",
        "latitude": 2.0469,
        "longitude": 45.3182,
        "location": "Mog",
        "name": "Ahmed",
        "phone": "0611",
        "notes": "chest pain",
    },
)
j = r.get_json() or {}
eid_med = j.get("id")
check("SOS medical", j.get("success") and eid_med, str(j))

r = c.post(
    "/api/send_alert",
    json={"type": "fire", "latitude": 2.05, "longitude": 45.32, "location": "Mog", "notes": "smoke"},
)
j = r.get_json() or {}
eid_fire = j.get("id")
check("SOS fire", j.get("success") and eid_fire, str(j))

r = c.post(
    "/api/send_alert",
    json={
        "type": "security",
        "latitude": 2.04,
        "longitude": 45.31,
        "location": "Mog",
        "notes": "theft",
    },
)
j = r.get_json() or {}
eid_sec = j.get("id")
check("SOS security", j.get("success") and eid_sec, str(j))

# Tracking
r = c.post(
    f"/api/emergencies/{eid_med}/location",
    json={"latitude": 2.047, "longitude": 45.319},
)
check("Location update", (r.get_json() or {}).get("success"))
r = c.get(f"/api/emergencies/{eid_med}/tracking")
check("Tracking payload", r.status_code == 200 and bool(r.get_json()), str(r.status_code))

# Notifications
r = c.get("/api/notifications")
check("Citizen notifications", r.status_code == 200, str(r.status_code))

# Dashboard
r = c.get("/api/user/dashboard")
check("User dashboard API", r.status_code == 200, str(r.status_code))

# Chat — text is canonical; message/content also accepted
r = c.post(f"/api/messages/{eid_med}", json={"message": "Hello hospital"})
j = r.get_json() or {}
check("Chat send citizen", r.status_code == 200 and j.get("success"), f"{r.status_code} {j}")

r = c.get(f"/api/messages/{eid_med}")
check("Chat get citizen", r.status_code == 200, str(r.status_code))

# Call Center initiate
r = c.post(
    "/api/call-center/initiate",
    json={"latitude": 2.0469, "longitude": 45.3182, "address": "Hodan", "phone": "0611"},
)
j = r.get_json() or {}
call_id = j.get("call_id")
check("CC initiate", j.get("success") and call_id, str(j))

c.get("/logout", follow_redirects=True)

# Operator
r = c.post(
    "/login",
    data={"username": "operator@callcenter.so", "password": "123456"},
    follow_redirects=True,
)
check("Login operator", r.status_code == 200)
r = c.get("/call-center")
check("CC dashboard page", r.status_code == 200, str(r.status_code))

r = c.post(f"/api/call-center/calls/{call_id}/answer", json={})
j = r.get_json() or {}
check("CC answer+AI", j.get("success") and "ai" in j, str(list(j.keys())))

r = c.post(
    f"/api/call-center/calls/{call_id}/ai/analyze",
    json={"notes": "chest pain emergency"},
)
j = r.get_json() or {}
rec = (j.get("panel") or {}).get("recommendation_id")
check("CC AI analyze", j.get("success") and rec, str((j.get("panel") or {}).get("category")))

r = c.post(
    f"/api/call-center/calls/{call_id}/ai/decision",
    json={"decision": "reject", "recommendation_id": rec},
)
check("CC AI reject", (r.get_json() or {}).get("success"))

r = c.post(
    f"/api/call-center/calls/{call_id}/dispatch",
    json={"types": ["medical"], "notes": "operator dispatch"},
)
check("CC manual dispatch", (r.get_json() or {}).get("success"), str(r.get_json()))

c.get("/logout", follow_redirects=True)

# Hospital
r = c.post(
    "/login",
    data={"username": "amina@hospital.com", "password": "123456"},
    follow_redirects=True,
)
check("Login hospital", r.status_code == 200)
r = c.get("/hospital")
check("Hospital page", r.status_code == 200, str(r.status_code))
r = c.get("/api/get_emergencies?type=medical")
check("Hospital emergencies API", r.status_code == 200, str(r.status_code))
r = c.post(f"/api/hospital/request/{eid_med}/accept", json={})
j = r.get_json() or {}
check("Hospital accept", r.status_code == 200 and j.get("success") is not False, str(j))

c.get("/logout", follow_redirects=True)

# Police
r = c.post(
    "/login",
    data={"username": "hassan@police.com", "password": "123456"},
    follow_redirects=True,
)
check("Login police", r.status_code == 200)
r = c.get("/police")
check("Police page", r.status_code == 200)
r = c.get("/api/get_emergencies?type=police")
check("Police emergencies API", r.status_code == 200)
r = c.post("/api/update_status", json={"id": eid_sec, "status": "dispatched"})
check("Police update status", r.status_code == 200, str(r.get_json()))

c.get("/logout", follow_redirects=True)

# Fire
r = c.post(
    "/login",
    data={"username": "muse@fire.com", "password": "123456"},
    follow_redirects=True,
)
check("Login fire", r.status_code == 200)
r = c.get("/fire")
check("Fire page", r.status_code == 200)
r = c.get("/api/get_emergencies?type=fire")
check("Fire emergencies API", r.status_code == 200)
r = c.post("/api/update_status", json={"id": eid_fire, "status": "dispatched"})
check("Fire update status", r.status_code == 200, str(r.get_json()))

c.get("/logout", follow_redirects=True)

# Admin
r = c.post(
    "/login",
    data={"username": "admin@emergency.so", "password": "admin123"},
    follow_redirects=True,
)
check("Login admin", r.status_code == 200)
r = c.get("/admin")
check("Admin page", r.status_code == 200)
r = c.get("/api/admin/stats")
check("Admin stats", r.status_code == 200 and bool(r.get_json()))
r = c.get("/api/admin/ai/stats")
check("Admin AI stats", (r.get_json() or {}).get("success"))
r = c.get("/api/admin/call-center/stats")
check("Admin CC stats", (r.get_json() or {}).get("success"))
r = c.get("/api/admin/users")
check("Admin users", r.status_code == 200)

# MySQL availability (optional — do not fail suite if unreachable)
try:
    from database import mysql_store

    mysql_ok = mysql_store.available()
    check("MySQL driver available (PyMySQL)", mysql_ok)
except Exception as exc:
    check("MySQL driver available (PyMySQL)", False, str(exc))

failed = [x for x in results if not x[1]]
print("\n=== SUMMARY ===")
print("passed", sum(1 for x in results if x[1]), "failed", len(failed), "total", len(results))
for item in failed:
    print("FAIL DETAIL:", item[0], "|", item[2])

shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if failed else 0)
