"""
Call Center AI helpers — analysis for operator sessions.

Uses AIEmergencyEngine.summarize_call (provider-agnostic).
Never dispatches; operators Approve / Reject / Manual.
"""

# Confidence below this shows a low-confidence banner (Sprint 3).
LOW_CONFIDENCE_THRESHOLD = 0.65


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


def _responder_flat(obj, role="hospital"):
    """Flatten a recommended responder for the Call Center panel DTO."""
    if not obj:
        return None
    detail = obj.get("score_detail") or {}
    flat = {
        "id": obj.get("id"),
        "name": obj.get("name") or obj.get("station_name"),
        "phone": obj.get("phone") or "",
        "distance_km": obj.get("distance_km"),
        "eta_minutes": obj.get("eta_minutes"),
        "active_load": (
            obj.get("active_load")
            if obj.get("active_load") is not None
            else detail.get("active_load")
        ),
    }
    if role == "hospital":
        flat["emergency_capacity"] = obj.get("emergency_capacity")
        flat["ambulance_available"] = bool(obj.get("ambulance_available"))
        if detail:
            flat["score_detail"] = detail
    return flat


def _ranking_why(hospital, police, fire, dispatch_reason):
    """Short ‘Why ranked #1’ line from existing score_detail / reason."""
    parts = []
    if hospital:
        detail = hospital.get("score_detail") or {}
        bits = []
        dist = hospital.get("distance_km")
        if dist is not None:
            bits.append(f"{dist} km")
        if hospital.get("ambulance_available"):
            bits.append("ambulance available")
        cap = hospital.get("emergency_capacity")
        if cap is not None:
            bits.append(f"capacity {cap}")
        load = hospital.get("active_load")
        if load is not None:
            bits.append(f"load {load}")
        if detail.get("source") == "nearest_precomputed":
            bits.append("nearest hospital")
        if bits:
            parts.append("Hospital: " + ", ".join(str(b) for b in bits))
    if police and police.get("distance_km") is not None:
        parts.append(f"Police: {police.get('distance_km')} km")
    if fire and fire.get("distance_km") is not None:
        parts.append(f"Fire: {fire.get('distance_km')} km")
    if parts:
        return "; ".join(parts)
    return (dispatch_reason or "").strip()[:240]


def panel_from_result(result):
    """Flatten analysis + recommendation for the Call Center AI panel UI."""
    analysis = (result or {}).get("analysis") or {}
    rec = (result or {}).get("recommendation") or {}
    conf = float(analysis.get("confidence") or rec.get("confidence") or 0)

    hospital = _responder_flat(rec.get("recommended_hospital"), "hospital")
    police = _responder_flat(rec.get("recommended_police"), "police")
    fire = _responder_flat(rec.get("recommended_fire"), "fire")

    snap = analysis.get("input_snapshot") or {}
    lat = snap.get("lat")
    lng = snap.get("lng")
    has_location = lat is not None and lng is not None
    has_responder = bool(hospital or police or fire)
    reason_text = (
        analysis.get("reason") or rec.get("reason") or ""
    ).lower()
    insufficient_data = (
        (not has_location)
        or (not has_responder)
        or ("insufficient" in reason_text)
    )
    low_confidence = conf < LOW_CONFIDENCE_THRESHOLD

    history = []
    # History may live on analysis context only via packed result extras — optional.
    hist_note = ""
    if isinstance((result or {}).get("emergency_history"), list):
        history = result["emergency_history"]
    if history:
        hist_note = f"Caller has {len(history)} prior emergency record(s)."

    dispatch_reason = rec.get("reason") or ""
    ranking_why = _ranking_why(hospital, police, fire, dispatch_reason)

    return {
        "emergency_type": analysis.get("gurmad_type") or analysis.get("category"),
        "category": analysis.get("category"),
        "priority": analysis.get("priority"),
        "severity": analysis.get("severity") or analysis.get("priority"),
        "risk": analysis.get("risk_level"),
        "confidence": conf,
        "confidence_pct": int(round(conf * 100)),
        "low_confidence": low_confidence,
        "insufficient_data": insufficient_data,
        "reason": analysis.get("reason") or rec.get("reason") or "",
        "summary": analysis.get("summary") or "",
        "required_services": analysis.get("required_services_label")
        or analysis.get("required_services"),
        "recommended_hospital": hospital,
        "recommended_police": police,
        "recommended_fire": fire,
        "eta_minutes": rec.get("estimated_arrival_minutes")
        or (hospital or {}).get("eta_minutes")
        or (police or {}).get("eta_minutes")
        or (fire or {}).get("eta_minutes"),
        "suggested_dispatch_types": rec.get("suggested_dispatch_types") or [],
        "recommendation_id": rec.get("id"),
        "analysis_id": analysis.get("id"),
        "status": rec.get("status") or "pending",
        "dispatch_reason": dispatch_reason,
        "ranking_why": ranking_why,
        "history_note": hist_note,
        "has_location": has_location,
    }
