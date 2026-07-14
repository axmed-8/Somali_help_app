"""
Persistence for AI analysis, recommendations, dispatch logs, and memory events.

Uses the same read_fn/save_fn pattern as call_center_logic (JSON or MySQL via app).
"""
from ai_engine.models import now_str

STORE_ANALYSIS = "ai_analysis"
STORE_RECOMMENDATION = "ai_recommendation"
STORE_DISPATCH_LOG = "ai_dispatch_log"
STORE_MEMORY = "ai_memory"


def _empty_list_store():
    return {"items": [], "next_id": 1}


def _load(store, read_fn):
    data = read_fn(store, _empty_list_store())
    data.setdefault("items", [])
    data.setdefault("next_id", 1)
    return data


def _save(store, data, save_fn):
    save_fn(store, data)


def save_analysis(record, read_fn, save_fn):
    data = _load(STORE_ANALYSIS, read_fn)
    rid = data["next_id"]
    data["next_id"] = rid + 1
    record = dict(record)
    record["id"] = rid
    record.setdefault("created_at", now_str())
    data["items"].append(record)
    _save(STORE_ANALYSIS, data, save_fn)
    return record


def get_analysis(analysis_id, read_fn):
    data = _load(STORE_ANALYSIS, read_fn)
    for item in data["items"]:
        if item.get("id") == analysis_id:
            return item
    return None


def get_analysis_for_emergency(emergency_id, read_fn):
    data = _load(STORE_ANALYSIS, read_fn)
    matches = [i for i in data["items"] if i.get("emergency_id") == emergency_id]
    if not matches:
        return None
    return max(matches, key=lambda x: x.get("id", 0))


def get_analysis_for_call(call_id, read_fn):
    data = _load(STORE_ANALYSIS, read_fn)
    matches = [i for i in data["items"] if i.get("call_id") == call_id]
    if not matches:
        return None
    return max(matches, key=lambda x: x.get("id", 0))


def list_analyses(read_fn, limit=100):
    data = _load(STORE_ANALYSIS, read_fn)
    items = sorted(data["items"], key=lambda x: x.get("id", 0), reverse=True)
    return items[:limit]


def save_recommendation(record, read_fn, save_fn):
    data = _load(STORE_RECOMMENDATION, read_fn)
    rid = data["next_id"]
    data["next_id"] = rid + 1
    record = dict(record)
    record["id"] = rid
    record.setdefault("created_at", now_str())
    record.setdefault("status", "pending")
    data["items"].append(record)
    _save(STORE_RECOMMENDATION, data, save_fn)
    return record


def get_recommendation(rec_id, read_fn):
    data = _load(STORE_RECOMMENDATION, read_fn)
    for item in data["items"]:
        if item.get("id") == rec_id:
            return item
    return None


def get_recommendation_for_emergency(emergency_id, read_fn):
    data = _load(STORE_RECOMMENDATION, read_fn)
    matches = [i for i in data["items"] if i.get("emergency_id") == emergency_id]
    if not matches:
        return None
    return max(matches, key=lambda x: x.get("id", 0))


def get_recommendation_for_call(call_id, read_fn):
    data = _load(STORE_RECOMMENDATION, read_fn)
    matches = [i for i in data["items"] if i.get("call_id") == call_id]
    if not matches:
        return None
    return max(matches, key=lambda x: x.get("id", 0))


def update_recommendation(rec_id, updates, read_fn, save_fn):
    data = _load(STORE_RECOMMENDATION, read_fn)
    for item in data["items"]:
        if item.get("id") == rec_id:
            item.update(updates)
            item["updated_at"] = now_str()
            _save(STORE_RECOMMENDATION, data, save_fn)
            return item
    return None


def save_dispatch_log(record, read_fn, save_fn):
    data = _load(STORE_DISPATCH_LOG, read_fn)
    rid = data["next_id"]
    data["next_id"] = rid + 1
    record = dict(record)
    record["id"] = rid
    record.setdefault("created_at", now_str())
    data["items"].append(record)
    _save(STORE_DISPATCH_LOG, data, save_fn)
    return record


def list_dispatch_logs(read_fn, limit=100):
    data = _load(STORE_DISPATCH_LOG, read_fn)
    items = sorted(data["items"], key=lambda x: x.get("id", 0), reverse=True)
    return items[:limit]


def append_memory_event(event, read_fn, save_fn):
    """
    Append a durable memory event for future strategic AI modules.

    event_type examples:
      analysis | recommendation | human_decision | dispatch_result | outcome
    """
    data = _load(STORE_MEMORY, read_fn)
    eid = data["next_id"]
    data["next_id"] = eid + 1
    record = dict(event)
    record["id"] = eid
    record.setdefault("timestamp", now_str())
    data["items"].append(record)
    _save(STORE_MEMORY, data, save_fn)
    return record


def list_memory_events(read_fn, event_type=None, emergency_id=None, limit=200):
    data = _load(STORE_MEMORY, read_fn)
    items = data["items"]
    if event_type:
        items = [i for i in items if i.get("event_type") == event_type]
    if emergency_id is not None:
        items = [i for i in items if i.get("emergency_id") == emergency_id]
    items = sorted(items, key=lambda x: x.get("id", 0), reverse=True)
    return items[:limit]


def memory_stats_today(read_fn):
    """Lightweight counters for admin AI Intelligence section."""
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    analyses = _load(STORE_ANALYSIS, read_fn)["items"]
    recs = _load(STORE_RECOMMENDATION, read_fn)["items"]
    logs = _load(STORE_DISPATCH_LOG, read_fn)["items"]

    def _is_today(ts):
        return str(ts or "").startswith(today)

    today_analyses = [a for a in analyses if _is_today(a.get("created_at"))]
    confidences = [float(a.get("confidence") or 0) for a in today_analyses if a.get("confidence") is not None]
    approved = sum(1 for r in recs if r.get("status") == "approved" and _is_today(r.get("updated_at") or r.get("created_at")))
    rejected = sum(1 for r in recs if r.get("status") == "rejected" and _is_today(r.get("updated_at") or r.get("created_at")))
    manual = sum(1 for r in recs if r.get("status") == "manual" and _is_today(r.get("updated_at") or r.get("created_at")))

    return {
        "decisions_today": len(today_analyses),
        "average_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
        "approved_recommendations": approved,
        "rejected_recommendations": rejected,
        "manual_selections": manual,
        "dispatch_logs_today": sum(1 for x in logs if _is_today(x.get("created_at"))),
        "total_memory_events": len(_load(STORE_MEMORY, read_fn)["items"]),
    }
