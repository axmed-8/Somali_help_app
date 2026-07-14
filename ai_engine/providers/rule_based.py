"""
Built-in rule-based AI provider (default).

Deterministic heuristics for Somalia emergency triage and nearest-responder
recommendations. No external API. Safe for offline / low-latency use.
"""
import math
import re

from ai_engine.models import (
    TYPE_TO_CATEGORY,
    CATEGORY_TO_TYPE,
    empty_analysis,
    empty_recommendation,
    services_label,
)
from ai_engine.providers.base import AIProvider


# Keyword signals → category boosts
_CATEGORY_KEYWORDS = {
    "medical": (
        r"\b(medical|ambulance|heart|chest pain|stroke|bleed|bleeding|unconscious|"
        r"pregnant|labor|seizure|asthma|diabetes|injury|wounded|sick|ill|pain|"
        r"hospital|doctor|overdose|breathing|cpr)\b",
    ),
    "fire": (
        r"\b(fire|smoke|burn|burning|flame|explosion|gas leak|electrical fire|"
        r"building on fire|rescue)\b",
    ),
    "police": (
        r"\b(police|robbery|theft|assault|attack|gun|shot|shooting|stab|kidnap|"
        r"violence|threat|crime|security|fight|armed)\b",
    ),
    "accident": (
        r"\b(accident|crash|collision|car|vehicle|traffic|hit and run|rollover|"
        r"road accident|motorbike)\b",
    ),
    "family_emergency": (
        r"\b(family|relative|missing (person|child)|domestic|help my|"
        r"can't find|lost child)\b",
    ),
}

_CRITICAL_KEYWORDS = (
    r"\b(critical|dying|unconscious|not breathing|gunshot|explosion|mass|"
    r"multiple victims|severe bleeding|cardiac|choking)\b"
)
_HIGH_KEYWORDS = (
    r"\b(urgent|serious|heavy bleeding|fire spreading|armed|hostage|"
    r"trapped|major|severe)\b"
)


def _text_blob(context):
    parts = [
        context.get("type") or "",
        context.get("notes") or "",
        context.get("description") or "",
        context.get("transcript") or "",
        context.get("summary") or "",
    ]
    return " ".join(str(p) for p in parts).lower()


def _haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _eta_minutes(dist_km):
    if dist_km is None:
        return None
    return max(3, int((float(dist_km) / 40) * 60))


class RuleBasedProvider(AIProvider):
    name = "rule_based"

    def analyze_emergency(self, context):
        context = context or {}
        text = _text_blob(context)
        declared = (context.get("type") or "").strip().lower().replace(" ", "_")
        if declared in ("police",):
            declared = "security"
        if declared in ("family", "family_emergency"):
            declared = "family_help"

        scores = {c: 0.0 for c in TYPE_TO_CATEGORY.values() if c != "other"}
        scores["other"] = 0.1

        if declared and declared in TYPE_TO_CATEGORY:
            cat = TYPE_TO_CATEGORY[declared]
            scores[cat] = scores.get(cat, 0) + 3.0

        for cat, patterns in _CATEGORY_KEYWORDS.items():
            for pat in patterns:
                if re.search(pat, text, re.I):
                    scores[cat] = scores.get(cat, 0) + 2.0

        # Accident often needs medical + police
        if scores.get("accident", 0) >= 2:
            scores["medical"] = scores.get("medical", 0) + 1.0
            scores["police"] = scores.get("police", 0) + 0.5

        category = max(scores, key=scores.get)
        if scores[category] < 1.0:
            category = TYPE_TO_CATEGORY.get(declared, "other") if declared else "other"

        gurmad_type = CATEGORY_TO_TYPE.get(category, "medical")
        services = self._required_services(category, text, scores)
        priority, risk = self._priority_and_risk(category, text, services, context)

        top = scores[category]
        second = sorted(scores.values(), reverse=True)[1] if len(scores) > 1 else 0
        confidence = min(0.98, 0.55 + (top * 0.08) + max(0, (top - second) * 0.05))
        if declared and TYPE_TO_CATEGORY.get(declared) == category:
            confidence = min(0.98, confidence + 0.12)

        reason_parts = [
            f"Classified as {category.replace('_', ' ')} "
            f"(declared type: {declared or 'none'})."
        ]
        if services:
            reason_parts.append(f"Required services: {services_label(services)}.")
        reason_parts.append(f"Priority {priority}, risk {risk}.")

        summary = (context.get("notes") or context.get("description") or "").strip()
        if not summary:
            summary = f"{category.replace('_', ' ').title()} emergency — {priority} priority."

        return empty_analysis(
            emergency_id=context.get("emergency_id"),
            call_id=context.get("call_id"),
            category=category,
            gurmad_type=gurmad_type,
            priority=priority,
            risk_level=risk,
            required_services=services,
            required_services_label=services_label(services),
            confidence=round(confidence, 4),
            reason=" ".join(reason_parts),
            summary=summary[:500],
            provider=self.name,
            source=context.get("source") or "sos",
            input_snapshot={
                "type": declared,
                "has_notes": bool(text.strip()),
                "lat": context.get("latitude"),
                "lng": context.get("longitude"),
            },
        )

    def recommend_dispatch(self, analysis, context):
        context = context or {}
        analysis = analysis or {}
        lat, lng = context.get("latitude"), context.get("longitude")
        services = set(analysis.get("required_services") or [])
        nearest = context.get("nearest") or {}
        hospitals = list(context.get("hospitals") or [])
        active = list(context.get("active_emergencies") or [])

        hospital_rec = None
        police_rec = None
        fire_rec = None
        scoring = {}
        reasons = []

        need_medical = bool(services & {"medical", "ambulance"}) or analysis.get("category") in (
            "medical", "accident", "family_emergency",
        )
        need_police = bool(services & {"police"}) or analysis.get("category") in (
            "police", "accident",
        )
        need_fire = bool(services & {"fire"}) or analysis.get("category") == "fire"

        if need_medical and lat is not None and lng is not None:
            hospital_rec = self._pick_hospital(lat, lng, hospitals, nearest, active)
            if hospital_rec:
                scoring["hospital"] = hospital_rec.get("score_detail", {})
                reasons.append(
                    f"{hospital_rec['name']}: closest suitable hospital"
                    + (f", ETA {hospital_rec.get('eta_minutes')} min." if hospital_rec.get("eta_minutes") else ".")
                )
                if hospital_rec.get("ambulance_available"):
                    reasons.append("Ambulance available.")
                if hospital_rec.get("emergency_capacity"):
                    reasons.append(f"Capacity {hospital_rec['emergency_capacity']}.")

        if need_police:
            police_rec = self._pick_station(
                "police", lat, lng, context.get("police_station"), nearest, active
            )
            if police_rec:
                reasons.append(
                    f"Police: {police_rec['name']} "
                    f"(~{police_rec.get('distance_km')} km, ETA {police_rec.get('eta_minutes')} min)."
                )

        if need_fire:
            fire_rec = self._pick_station(
                "fire", lat, lng, context.get("fire_station"), nearest, active
            )
            if fire_rec:
                reasons.append(
                    f"Fire: {fire_rec['name']} "
                    f"(~{fire_rec.get('distance_km')} km, ETA {fire_rec.get('eta_minutes')} min)."
                )

        etas = [
            r.get("eta_minutes")
            for r in (hospital_rec, police_rec, fire_rec)
            if r and r.get("eta_minutes") is not None
        ]
        eta = min(etas) if etas else None

        dispatch_types = []
        if need_medical:
            dispatch_types.append(
                "medical" if analysis.get("category") != "family_emergency" else "family_help"
            )
        if need_police and analysis.get("category") == "accident":
            dispatch_types.append("accident")
        elif need_police:
            dispatch_types.append("security")
        if need_fire:
            dispatch_types.append("fire")
        # Deduplicate while preserving order
        seen = set()
        dispatch_types = [t for t in dispatch_types if not (t in seen or seen.add(t))]

        confidence = float(analysis.get("confidence") or 0.7)
        if hospital_rec or police_rec or fire_rec:
            confidence = min(0.98, confidence + 0.05)

        return empty_recommendation(
            analysis_id=analysis.get("id"),
            emergency_id=analysis.get("emergency_id") or context.get("emergency_id"),
            call_id=analysis.get("call_id") or context.get("call_id"),
            recommended_hospital=hospital_rec,
            recommended_police=police_rec,
            recommended_fire=fire_rec,
            estimated_arrival_minutes=eta,
            suggested_dispatch_types=dispatch_types,
            reason=" ".join(reasons) if reasons else "Insufficient location or resource data for a firm recommendation.",
            confidence=round(confidence, 4),
            scoring=scoring,
            provider=self.name,
            status="pending",
        )

    def _required_services(self, category, text, scores):
        if category == "fire":
            services = ["fire"]
            if scores.get("medical", 0) >= 1.5 or re.search(r"\b(injur|burn|trapped|ambulance)\b", text):
                services.append("medical")
            if scores.get("police", 0) >= 1.5:
                services.append("police")
            return services
        if category == "police":
            return ["police"]
        if category == "accident":
            return ["medical", "police"]
        if category == "family_emergency":
            return ["medical"]
        if category == "medical":
            services = ["medical"]
            if scores.get("police", 0) >= 2.0:
                services.append("police")
            return services
        return ["medical"]

    def _priority_and_risk(self, category, text, services, context):
        priority = "medium"
        risk = "medium"
        if re.search(_CRITICAL_KEYWORDS, text, re.I):
            priority, risk = "critical", "critical"
        elif re.search(_HIGH_KEYWORDS, text, re.I):
            priority, risk = "high", "high"
        elif category in ("fire", "accident"):
            priority, risk = "high", "high"
        elif category == "police":
            priority, risk = "high", "medium"
        elif category == "family_emergency":
            priority, risk = "medium", "low"
        elif category == "other":
            priority, risk = "low", "low"

        hist = context.get("emergency_history") or []
        if len(hist) >= 3 and priority in ("low", "medium"):
            priority = "medium"
            risk = "medium" if risk == "low" else risk

        if len(services) >= 3:
            if priority == "medium":
                priority = "high"
            if risk == "medium":
                risk = "high"
        return priority, risk

    def _pick_hospital(self, lat, lng, hospitals, nearest, active):
        if nearest.get("hospital"):
            nh = nearest["hospital"]
            return {
                "id": nh.get("id"),
                "name": nh.get("name"),
                "phone": nh.get("phone", ""),
                "latitude": nh.get("latitude"),
                "longitude": nh.get("longitude"),
                "distance_km": nh.get("distance_km"),
                "eta_minutes": nh.get("eta_minutes") or _eta_minutes(nh.get("distance_km")),
                "ambulance_available": nh.get("ambulance_available", False),
                "emergency_capacity": nh.get("emergency_capacity"),
                "score_detail": {"source": "nearest_precomputed"},
            }

        best = None
        best_score = -1e9
        # Count active load per hospital
        load = {}
        for em in active:
            hid = em.get("assigned_hospital_id")
            if hid and em.get("status") not in ("resolved", "completed", "cancelled"):
                load[hid] = load.get(hid, 0) + 1

        for h in hospitals:
            if h.get("operating_status", h.get("status", "open")) not in ("open", "limited"):
                continue
            if h.get("emergency_capacity", 1) <= 0:
                continue
            hlat, hlng = h.get("latitude"), h.get("longitude")
            if hlat is None or hlng is None:
                continue
            dist = _haversine_km(float(lat), float(lng), float(hlat), float(hlng))
            eta = _eta_minutes(dist)
            # Lower distance/load is better; capacity and ambulance boost score
            score = 100.0 - (dist * 8.0) - (load.get(h["id"], 0) * 5.0)
            score += min(20, float(h.get("emergency_capacity") or 0))
            if h.get("ambulance_available"):
                score += 12
            if "ICU" in (h.get("services") or []) or "Trauma" in (h.get("services") or []):
                score += 8
            if score > best_score:
                best_score = score
                best = {
                    "id": h["id"],
                    "name": h.get("name"),
                    "phone": h.get("phone", ""),
                    "latitude": hlat,
                    "longitude": hlng,
                    "distance_km": round(dist, 2),
                    "eta_minutes": eta,
                    "ambulance_available": bool(h.get("ambulance_available")),
                    "emergency_capacity": h.get("emergency_capacity"),
                    "score_detail": {
                        "score": round(score, 2),
                        "distance_km": round(dist, 2),
                        "active_load": load.get(h["id"], 0),
                    },
                }
        return best

    def _pick_station(self, kind, lat, lng, station, nearest, active):
        pre = (nearest or {}).get(kind)
        if pre:
            return {
                "id": pre.get("id", kind),
                "name": pre.get("name"),
                "phone": pre.get("phone", ""),
                "latitude": pre.get("latitude"),
                "longitude": pre.get("longitude"),
                "distance_km": pre.get("distance_km"),
                "eta_minutes": pre.get("eta_minutes") or _eta_minutes(pre.get("distance_km")),
            }
        st = station or {}
        if not st or lat is None or lng is None:
            return None
        if st.get("latitude") is None:
            return None
        dist = _haversine_km(float(lat), float(lng), float(st["latitude"]), float(st["longitude"]))
        # Soft workload penalty from active emergencies of matching type
        load = 0
        for em in active or []:
            if kind == "police" and em.get("assigned_to") == "police":
                if em.get("status") not in ("resolved", "completed", "cancelled"):
                    load += 1
            if kind == "fire" and em.get("assigned_to") == "fire":
                if em.get("status") not in ("resolved", "completed", "cancelled"):
                    load += 1
        return {
            "id": st.get("id", kind),
            "name": st.get("name", kind.title()),
            "phone": st.get("phone", ""),
            "latitude": st["latitude"],
            "longitude": st["longitude"],
            "distance_km": round(dist, 2),
            "eta_minutes": _eta_minutes(dist) + min(5, load),
            "active_load": load,
        }
