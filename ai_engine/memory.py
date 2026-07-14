"""
AI Memory layer — durable historical knowledge for future strategic modules.

Stores every AI analysis, human decision, dispatch result, and final outcome.
Later consumers: annual reports, hospital planning, crime analysis, fire hotspots,
public safety intelligence, infrastructure recommendations.
"""
from ai_engine import storage as store
from ai_engine.models import now_str


class AIMemory:
    """Append-only memory of AI + human emergency decision history."""

    def __init__(self, read_fn, save_fn):
        self.read_fn = read_fn
        self.save_fn = save_fn

    def record_analysis(self, analysis):
        return store.append_memory_event(
            {
                "event_type": "analysis",
                "emergency_id": analysis.get("emergency_id"),
                "call_id": analysis.get("call_id"),
                "analysis_id": analysis.get("id"),
                "payload": {
                    "category": analysis.get("category"),
                    "priority": analysis.get("priority"),
                    "risk_level": analysis.get("risk_level"),
                    "required_services": analysis.get("required_services"),
                    "confidence": analysis.get("confidence"),
                    "reason": analysis.get("reason"),
                    "provider": analysis.get("provider"),
                    "source": analysis.get("source"),
                },
                "timestamp": analysis.get("created_at") or now_str(),
            },
            self.read_fn,
            self.save_fn,
        )

    def record_recommendation(self, recommendation):
        return store.append_memory_event(
            {
                "event_type": "recommendation",
                "emergency_id": recommendation.get("emergency_id"),
                "call_id": recommendation.get("call_id"),
                "recommendation_id": recommendation.get("id"),
                "analysis_id": recommendation.get("analysis_id"),
                "payload": {
                    "hospital": (recommendation.get("recommended_hospital") or {}).get("name"),
                    "police": (recommendation.get("recommended_police") or {}).get("name"),
                    "fire": (recommendation.get("recommended_fire") or {}).get("name"),
                    "eta_minutes": recommendation.get("estimated_arrival_minutes"),
                    "confidence": recommendation.get("confidence"),
                    "reason": recommendation.get("reason"),
                    "suggested_dispatch_types": recommendation.get("suggested_dispatch_types"),
                },
                "timestamp": recommendation.get("created_at") or now_str(),
            },
            self.read_fn,
            self.save_fn,
        )

    def record_human_decision(self, decision):
        """
        decision keys:
          emergency_id, call_id, recommendation_id, decision (approve|reject|manual),
          operator_id, operator_name, notes, selected_types (optional)
        """
        return store.append_memory_event(
            {
                "event_type": "human_decision",
                "emergency_id": decision.get("emergency_id"),
                "call_id": decision.get("call_id"),
                "recommendation_id": decision.get("recommendation_id"),
                "payload": {
                    "decision": decision.get("decision"),
                    "operator_id": decision.get("operator_id"),
                    "operator_name": decision.get("operator_name"),
                    "notes": decision.get("notes"),
                    "selected_types": decision.get("selected_types"),
                },
                "timestamp": decision.get("timestamp") or now_str(),
            },
            self.read_fn,
            self.save_fn,
        )

    def record_dispatch_result(self, result):
        """
        result keys:
          emergency_id, call_id, recommendation_id, dispatched_to, emergency_ids,
          assigned_hospital_id/name, human_decision, operator_id
        """
        log = store.save_dispatch_log(
            {
                "emergency_id": result.get("emergency_id"),
                "call_id": result.get("call_id"),
                "recommendation_id": result.get("recommendation_id"),
                "analysis_id": result.get("analysis_id"),
                "human_decision": result.get("human_decision"),
                "dispatched_to": result.get("dispatched_to") or [],
                "emergency_ids": result.get("emergency_ids") or [],
                "assigned_hospital_id": result.get("assigned_hospital_id"),
                "assigned_hospital_name": result.get("assigned_hospital_name"),
                "operator_id": result.get("operator_id"),
                "notes": result.get("notes", ""),
            },
            self.read_fn,
            self.save_fn,
        )
        store.append_memory_event(
            {
                "event_type": "dispatch_result",
                "emergency_id": result.get("emergency_id"),
                "call_id": result.get("call_id"),
                "dispatch_log_id": log.get("id"),
                "recommendation_id": result.get("recommendation_id"),
                "payload": {
                    "human_decision": result.get("human_decision"),
                    "dispatched_to": result.get("dispatched_to"),
                    "emergency_ids": result.get("emergency_ids"),
                    "assigned_hospital_name": result.get("assigned_hospital_name"),
                },
                "timestamp": log.get("created_at") or now_str(),
            },
            self.read_fn,
            self.save_fn,
        )
        return log

    def record_outcome(self, outcome):
        """
        Final emergency outcome for learning / strategic modules.
        outcome keys: emergency_id, status, final_status, duration hints, notes
        """
        return store.append_memory_event(
            {
                "event_type": "outcome",
                "emergency_id": outcome.get("emergency_id"),
                "call_id": outcome.get("call_id"),
                "payload": {
                    "status": outcome.get("status"),
                    "final_status": outcome.get("final_status"),
                    "assigned_to": outcome.get("assigned_to"),
                    "type": outcome.get("type"),
                    "district": outcome.get("district"),
                    "latitude": outcome.get("latitude"),
                    "longitude": outcome.get("longitude"),
                    "notes": outcome.get("notes"),
                },
                "timestamp": outcome.get("timestamp") or now_str(),
            },
            self.read_fn,
            self.save_fn,
        )

    def history_for_emergency(self, emergency_id, limit=50):
        return store.list_memory_events(
            self.read_fn, emergency_id=emergency_id, limit=limit
        )

    def all_events(self, event_type=None, limit=200):
        return store.list_memory_events(
            self.read_fn, event_type=event_type, limit=limit
        )
