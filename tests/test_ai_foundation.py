"""AI Emergency Engine foundation tests — provider abstraction, memory, non-blocking SOS."""
import os
import sys
import tempfile
import shutil
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def app_client():
    os.environ["GURMADNET_DB"] = "json"
    os.environ["AI_PROVIDER"] = "rule_based"
    os.environ["EMAIL_PROVIDER"] = "memory"
    import importlib
    import app as ers_app
    from ai_engine.factory import clear_provider_cache
    from ai_engine import service as ai_service
    from email_service.factory import clear_email_provider_cache
    from email_service.memory_provider import clear_outbox

    clear_provider_cache()
    clear_email_provider_cache()
    clear_outbox()
    ai_service._engine_singleton = None
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
    # Reset AI engine to use temp DB read/save
    ai_service._engine_singleton = None
    ers_app.app.config["TESTING"] = True
    ers_app.app.config["WTF_CSRF_ENABLED"] = False
    client = ers_app.app.test_client()
    yield client, ers_app
    ai_service._engine_singleton = None
    clear_provider_cache()
    shutil.rmtree(tmp, ignore_errors=True)


def login(client, email, password):
    return client.post(
        "/login", data={"username": email, "password": password}, follow_redirects=True
    )


def test_provider_factory_default_rule_based():
    from ai_engine.factory import get_provider, list_providers, clear_provider_cache

    clear_provider_cache()
    assert "rule_based" in list_providers()
    assert "openai" in list_providers()
    assert "gemini" in list_providers()
    assert "claude" in list_providers()
    assert "deepseek" in list_providers()
    assert "local" in list_providers()
    p = get_provider("rule_based", force_new=True)
    assert p.name == "rule_based"
    assert p.health_check() is True


def test_provider_interface_contract():
    from ai_engine.providers.base import AIProvider
    from ai_engine.providers.rule_based import RuleBasedProvider

    assert issubclass(RuleBasedProvider, AIProvider)
    provider = RuleBasedProvider()
    analysis = provider.analyze_emergency({
        "type": "medical",
        "notes": "Chest pain and difficulty breathing",
        "latitude": 2.05,
        "longitude": 45.32,
        "source": "sos",
    })
    assert analysis["category"] == "medical"
    assert analysis["priority"] in ("low", "medium", "high", "critical")
    assert 0 < analysis["confidence"] <= 1
    assert analysis["reason"]
    rec = provider.recommend_dispatch(analysis, {
        "latitude": 2.05,
        "longitude": 45.32,
        "hospitals": [{
            "id": 1,
            "name": "Banadir Hospital",
            "latitude": 2.052,
            "longitude": 45.325,
            "operating_status": "open",
            "emergency_capacity": 20,
            "ambulance_available": True,
            "services": ["Emergency", "ICU"],
        }],
        "police_station": {"name": "Police", "latitude": 2.038, "longitude": 45.315},
        "fire_station": {"name": "Fire", "latitude": 2.052, "longitude": 45.328},
        "active_emergencies": [],
    })
    assert rec["recommended_hospital"] is not None
    assert rec["recommended_hospital"]["name"] == "Banadir Hospital"
    assert rec["status"] == "pending"


def test_accident_requires_medical_and_police():
    from ai_engine.providers.rule_based import RuleBasedProvider

    p = RuleBasedProvider()
    analysis = p.analyze_emergency({
        "type": "accident",
        "notes": "Car crash with injuries",
    })
    assert analysis["category"] == "accident"
    assert "medical" in analysis["required_services"]
    assert "police" in analysis["required_services"]


def test_ai_memory_records_analysis_and_decision(app_client):
    _, ers_app = app_client
    from ai_engine.service import AIEmergencyEngine
    from ai_engine.factory import clear_provider_cache

    clear_provider_cache()
    engine = AIEmergencyEngine(ers_app.read_json, ers_app.save_json)
    result = engine.analyze_and_recommend({
        "emergency_id": 99,
        "type": "fire",
        "notes": "Building on fire, people trapped",
        "latitude": 2.05,
        "longitude": 45.33,
        "source": "sos",
        "hospitals": [],
        "police_station": ers_app.RESPONSE_STATIONS["police"],
        "fire_station": ers_app.RESPONSE_STATIONS["fire"],
        "active_emergencies": [],
    })
    assert result["analysis"]["id"]
    assert result["recommendation"]["id"]
    engine.record_human_decision({
        "emergency_id": 99,
        "recommendation_id": result["recommendation"]["id"],
        "decision": "approve",
        "operator_id": 1,
        "operator_name": "Operator",
    })
    hist = engine.memory.history_for_emergency(99)
    types = {e["event_type"] for e in hist}
    assert "analysis" in types
    assert "recommendation" in types
    assert "human_decision" in types


def test_sos_still_auto_dispatches_and_ai_runs_parallel(app_client):
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
            "notes": "Severe bleeding",
        },
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["success"] is True
    eid = data["id"]
    em, _ = ers_app.get_emergency_by_id(eid)
    # Existing SOS auto-dispatch must remain intact
    assert em.get("assigned_team_label")
    assert em.get("tracking_active") is True

    # Wait for async AI thread to persist analysis + memory event
    deadline = time.time() + 10
    analysis = None
    mem = []
    while time.time() < deadline:
        packed = ers_app._ai_engine().get_latest_for_emergency(eid)
        analysis = packed.get("analysis")
        mem = ers_app._ai_engine().memory.history_for_emergency(eid)
        if analysis and any(e.get("event_type") == "analysis" for e in mem):
            break
        time.sleep(0.1)
    assert analysis is not None
    assert analysis.get("emergency_id") == eid
    assert analysis.get("provider") == "rule_based"
    assert any(e.get("event_type") == "analysis" for e in mem)


def test_outcome_written_to_ai_memory(app_client):
    client, ers_app = app_client
    login(client, "ahmed@example.com", "123456")
    eid = client.post(
        "/api/send_alert",
        json={
            "type": "fire",
            "latitude": 2.05,
            "longitude": 45.32,
            "location": "test",
        },
    ).get_json()["id"]
    client.get("/logout", follow_redirects=True)
    login(client, "muse@fire.com", "123456")
    r = client.post("/api/update_status", json={"id": eid, "status": "completed"})
    assert r.status_code == 200
    mem = ers_app._ai_engine().memory.history_for_emergency(eid)
    assert any(e.get("event_type") == "outcome" for e in mem)


def test_business_logic_never_imports_vendor_providers():
    """GurmadNet app must only use the AI service facade."""
    import ast

    path = os.path.join(ROOT, "app.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    forbidden = {
        "openai",
        "anthropic",
        "google.generativeai",
        "ai_engine.providers.openai_provider",
        "ai_engine.providers.gemini_provider",
        "ai_engine.providers.claude_provider",
        "ai_engine.providers.deepseek_provider",
        "ai_engine.providers.local_provider",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in forbidden
            assert not any(
                node.module.startswith(f + ".") for f in (
                    "openai", "anthropic",
                )
            )


def _login_operator(client, ers_app):
    login(client, "operator@callcenter.so", "123456")


def test_call_center_ai_analyze_and_reject_does_not_dispatch(app_client):
    client, ers_app = app_client
    login(client, "ahmed@example.com", "123456")
    init = client.post(
        "/api/call-center/initiate",
        json={
            "latitude": 2.0469,
            "longitude": 45.3182,
            "address": "Mogadishu",
            "phone": "0611111111",
        },
    ).get_json()
    assert init["success"] is True
    call_id = init["call_id"]
    client.get("/logout", follow_redirects=True)

    _login_operator(client, ers_app)
    ans = client.post(f"/api/call-center/calls/{call_id}/answer", json={}).get_json()
    assert ans["success"] is True
    assert "ai" in ans

    analyzed = client.post(
        f"/api/call-center/calls/{call_id}/ai/analyze",
        json={"notes": "My house is burning and smoke is everywhere"},
    ).get_json()
    assert analyzed["success"] is True
    panel = analyzed["panel"]
    assert panel["recommendation_id"]
    assert panel["category"] == "fire" or panel["emergency_type"] == "fire"
    assert panel["confidence"] > 0

    before = len(ers_app.load_emergencies().get("emergencies", []))
    rejected = client.post(
        f"/api/call-center/calls/{call_id}/ai/decision",
        json={"decision": "reject", "recommendation_id": panel["recommendation_id"]},
    ).get_json()
    assert rejected["success"] is True
    after = len(ers_app.load_emergencies().get("emergencies", []))
    assert after == before  # reject must not dispatch


def test_call_center_ai_approve_uses_existing_dispatch(app_client):
    client, ers_app = app_client
    login(client, "ahmed@example.com", "123456")
    call_id = client.post(
        "/api/call-center/initiate",
        json={
            "latitude": 2.05,
            "longitude": 45.32,
            "address": "Hodan",
            "phone": "0611111111",
        },
    ).get_json()["call_id"]
    client.get("/logout", follow_redirects=True)

    _login_operator(client, ers_app)
    client.post(f"/api/call-center/calls/{call_id}/answer", json={})
    analyzed = client.post(
        f"/api/call-center/calls/{call_id}/ai/analyze",
        json={"notes": "Father has severe chest pain"},
    ).get_json()
    assert analyzed["success"] is True
    rec_id = analyzed["panel"]["recommendation_id"]
    before = len(ers_app.load_emergencies().get("emergencies", []))
    approved = client.post(
        f"/api/call-center/calls/{call_id}/ai/decision",
        json={"decision": "approve", "recommendation_id": rec_id},
    ).get_json()
    assert approved["success"] is True
    assert approved["decision"] == "approve"
    assert approved.get("emergencies")
    after = len(ers_app.load_emergencies().get("emergencies", []))
    assert after > before


def test_admin_ai_stats(app_client):
    client, ers_app = app_client
    login(client, "admin@emergency.so", "admin123")
    r = client.get("/api/admin/ai/stats")
    assert r.status_code == 200
    data = r.get_json()
    assert data["success"] is True
    assert "stats" in data
    assert "decisions_today" in data["stats"]
