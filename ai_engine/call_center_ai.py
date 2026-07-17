"""
Call Center AI helpers — analysis for operator sessions.

Uses AIEmergencyEngine.summarize_call (provider-agnostic).
Never dispatches; operators Approve / Reject / Manual.
"""


def build_call_context(call, notes=None, extra=None):
    """Normalize a call_center_calls record into an AI context dict."""
    call = call or {}
    text = notes if notes is not None else (call.get("notes") or "")
    ctx = {
        "call_id": call.get("id"),
        "emergency_id": None,
        "type": call.get("emergency_type") or call.get("type") or "",
        "notes": text,
        "description": text,
        "transcript": call.get("transcript") or "",
        "latitude": call.get("latitude"),
        "longitude": call.get("longitude"),
        "address": call.get("address") or call.get("location") or "",
        "district": call.get("district") or "",
        "caller_name": call.get("caller_name"),
        "phone": call.get("phone"),
        "user_id": call.get("user_id"),
        "source": "call_center",
        "emergency_history": call.get("emergency_history") or [],
        "nearest": call.get("nearest") or {},
    }
    if extra:
        ctx.update(extra)
    return ctx


def analyze_call(engine, context):
    """Run Call Center AI (analysis + smart dispatch recommendation)."""
    return engine.summarize_call(context or {})


def panel_from_result(result):
    """Flatten analysis + recommendation for the Call Center AI panel UI."""
    analysis = (result or {}).get("analysis") or {}
    rec = (result or {}).get("recommendation") or {}
    conf = float(analysis.get("confidence") or rec.get("confidence") or 0)
    return {
        "emergency_type": analysis.get("gurmad_type") or analysis.get("category"),
        "category": analysis.get("category"),
        "priority": analysis.get("priority"),
        "severity": analysis.get("severity") or analysis.get("priority"),
        "risk": analysis.get("risk_level"),
        "confidence": conf,
        "confidence_pct": int(round(conf * 100)),
        "reason": analysis.get("reason") or rec.get("reason") or "",
        "summary": analysis.get("summary") or "",
        "required_services": analysis.get("required_services_label")
        or analysis.get("required_services"),
        "recommended_hospital": rec.get("recommended_hospital"),
        "recommended_police": rec.get("recommended_police"),
        "recommended_fire": rec.get("recommended_fire"),
        "eta_minutes": rec.get("estimated_arrival_minutes"),
        "suggested_dispatch_types": rec.get("suggested_dispatch_types") or [],
        "recommendation_id": rec.get("id"),
        "analysis_id": analysis.get("id"),
        "status": rec.get("status") or "pending",
        "dispatch_reason": rec.get("reason") or "",
    }
