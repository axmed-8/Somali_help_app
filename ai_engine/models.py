"""Shared AI decision / recommendation structures (provider-agnostic)."""
from datetime import datetime


CATEGORIES = (
    "medical",
    "fire",
    "police",
    "accident",
    "family_emergency",
    "other",
)

PRIORITIES = ("low", "medium", "high", "critical")

RISK_LEVELS = ("low", "medium", "high", "critical")

# Maps GurmadNet emergency types <-> AI categories
TYPE_TO_CATEGORY = {
    "medical": "medical",
    "fire": "fire",
    "security": "police",
    "police": "police",
    "accident": "accident",
    "family_help": "family_emergency",
    "family_emergency": "family_emergency",
    "other": "other",
}

CATEGORY_TO_TYPE = {
    "medical": "medical",
    "fire": "fire",
    "police": "security",
    "accident": "accident",
    "family_emergency": "family_help",
    "other": "medical",
}

SERVICE_COMBOS = (
    "medical_only",
    "police_only",
    "fire_only",
    "medical_police",
    "fire_ambulance",
    "fire_police",
    "medical_fire_police",
    "none",
)


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def empty_analysis(**overrides):
    base = {
        "id": None,
        "emergency_id": None,
        "call_id": None,
        "category": "other",
        "gurmad_type": "medical",
        "priority": "medium",
        "risk_level": "medium",
        "required_services": ["medical"],
        "required_services_label": "Medical only",
        "confidence": 0.0,
        "reason": "",
        "summary": "",
        "provider": "rule_based",
        "source": "sos",
        "input_snapshot": {},
        "created_at": now_str(),
    }
    base.update(overrides)
    return base


def empty_recommendation(**overrides):
    base = {
        "id": None,
        "analysis_id": None,
        "emergency_id": None,
        "call_id": None,
        "recommended_hospital": None,
        "recommended_police": None,
        "recommended_fire": None,
        "estimated_arrival_minutes": None,
        "suggested_dispatch_types": [],
        "reason": "",
        "confidence": 0.0,
        "scoring": {},
        "provider": "rule_based",
        "status": "pending",  # pending | approved | rejected | manual
        "created_at": now_str(),
    }
    base.update(overrides)
    return base


def services_label(services):
    """Human-readable required-services label."""
    s = set(services or [])
    if s >= {"medical", "fire", "police"}:
        return "Medical + Fire + Police"
    if s == {"medical", "police"}:
        return "Medical + Police"
    if s == {"fire", "medical"} or s == {"fire", "ambulance"}:
        return "Fire + Ambulance"
    if s == {"fire", "police"}:
        return "Fire + Police"
    if s == {"medical"} or s == {"ambulance"}:
        return "Medical only"
    if s == {"police"}:
        return "Police only"
    if s == {"fire"}:
        return "Fire only"
    if not s:
        return "None"
    return " + ".join(sorted(x.title() for x in s))
