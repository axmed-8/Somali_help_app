"""Socket.IO signaling smoke: initiate -> incoming -> accept -> end sync."""
from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ["GURMADNET_DB"] = "json"
os.environ["EMAIL_PROVIDER"] = "memory"
os.environ["TESTING"] = "1"

from email_service.factory import clear_email_provider_cache
from email_service.memory_provider import clear_outbox
from werkzeug.security import generate_password_hash

clear_email_provider_cache()
clear_outbox()

import app as ers_app

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
for name, email, role, phone in [
    ("Cit", "c@test.so", "citizen", "061"),
    ("Op", "o@test.so", "call_center", "062"),
]:
    uid = udata["next_id"]
    udata["next_id"] += 1
    udata["users"].append(
        {
            "id": uid,
            "name": name,
            "email": email,
            "phone": phone,
            "password_hash": generate_password_hash("123456"),
            "role": role,
            "status": "active",
            "email_verified": True,
            "created_at": ers_app.now_str(),
            "last_login": None,
            "activity": [],
        }
    )
ers_app.save_users(udata)
ers_app.app.config["TESTING"] = True
ers_app.app.config["WTF_CSRF_ENABLED"] = False

assert ers_app.socketio is not None
flask_client = ers_app.app.test_client()

flask_client.post("/login", data={"username": "c@test.so", "password": "123456"})
r = flask_client.post(
    "/api/call-center/initiate",
    json={"latitude": 2.04, "longitude": 45.31, "name": "Cit", "phone": "061"},
)
data = r.get_json()
assert data["success"] and data["voice_mode"] and "tel_href" not in data
call_id = data["call_id"]
print("call_id", call_id)

cit_sock = ers_app.socketio.test_client(ers_app.app, flask_test_client=flask_client)
op_http = ers_app.app.test_client()
op_http.post("/login", data={"username": "o@test.so", "password": "123456"})
op_sock = ers_app.socketio.test_client(ers_app.app, flask_test_client=op_http)

cit_sock.get_received()
op_sock.get_received()

cit_sock.emit("call:join", {"call_id": call_id})
cit_sock.emit("call:start", {"call_id": call_id})
time.sleep(0.1)
op_recv = op_sock.get_received()
incoming = [p for p in op_recv if p["name"] == "call:incoming"]
assert incoming, op_recv
print("incoming ok", incoming[0]["args"][0]["call_id"])

op_sock.emit("call:join", {"call_id": call_id})
op_sock.emit("call:accept", {"call_id": call_id})
time.sleep(0.1)
cit_recv = cit_sock.get_received()
accepted = [p for p in cit_recv if p["name"] == "call:accept"]
assert accepted, cit_recv
print("accept synced to citizen", accepted[0]["args"][0]["status"])

op_sock.emit("call:end", {"call_id": call_id})
time.sleep(0.1)
cit_end = [p for p in cit_sock.get_received() if p["name"] == "call:end"]
assert cit_end, "citizen did not get call:end"
print("end synced", cit_end[0]["args"][0])

import call_center_logic as cc

call = cc.get_call_by_id(cc.load_calls(ers_app.read_json, ers_app.save_json), call_id)
assert call["status"] == "ended" and call["ended_by"] == "operator"
print("PASS signaling sync")

# --- Race: operator accepts BEFORE citizen joins call room ---
flask_client2 = ers_app.app.test_client()
flask_client2.post("/login", data={"username": "c@test.so", "password": "123456"})
r2 = flask_client2.post(
    "/api/call-center/initiate",
    json={"latitude": 2.05, "longitude": 45.32, "name": "Cit", "phone": "061"},
)
call_id2 = r2.get_json()["call_id"]
cit2 = ers_app.socketio.test_client(ers_app.app, flask_test_client=flask_client2)
op2_http = ers_app.app.test_client()
op2_http.post("/login", data={"username": "o@test.so", "password": "123456"})
op2 = ers_app.socketio.test_client(ers_app.app, flask_test_client=op2_http)
cit2.get_received()
op2.get_received()
# Operator accepts immediately (citizen not yet in call_* room)
op2.emit("call:accept", {"call_id": call_id2})
time.sleep(0.1)
# Citizen personal room must still receive accept
cit_early = [p for p in cit2.get_received() if p["name"] == "call:accept"]
assert cit_early, "citizen missed call:accept via user room"
print("early-accept via user room ok")
# Citizen joins late — must also get catch-up accept
cit2.emit("call:join", {"call_id": call_id2})
time.sleep(0.1)
joined = [p for p in cit2.get_received() if p["name"] in ("call:joined", "call:accept")]
assert any(p["name"] == "call:joined" for p in joined)
assert any(
    p["name"] == "call:accept" or (p["name"] == "call:joined" and p["args"][0].get("status") == "connecting")
    for p in joined
)
print("late-join catch-up ok")
print("PASS race-safe accept")
shutil.rmtree(tmp, ignore_errors=True)
