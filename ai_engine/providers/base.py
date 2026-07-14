"""Abstract AI provider interface — all providers must implement this contract."""
from abc import ABC, abstractmethod


class AIProvider(ABC):
    """
    Provider-independent contract for the GurmadNet AI Emergency Engine.

    Implementations must never dispatch responders. They only analyze and recommend.
    """

    name = "base"

    @abstractmethod
    def analyze_emergency(self, context):
        """
        Analyze an emergency or call-center intake.

        context keys (all optional except description/type/notes when available):
          emergency_id, call_id, type, notes, description, transcript,
          latitude, longitude, address, district, caller_name, phone,
          emergency_history (list), active_emergencies (list),
          source ('sos'|'call_center'|'healthcare')

        Returns dict matching models.empty_analysis fields (without id/created_at required).
        """
        raise NotImplementedError

    @abstractmethod
    def recommend_dispatch(self, analysis, context):
        """
        Recommend responders after analysis.

        context may include:
          latitude, longitude, hospitals (list), police_station, fire_station,
          active_emergencies, nearest (precomputed), hospital_capacity hints

        Returns dict matching models.empty_recommendation fields.
        """
        raise NotImplementedError

    def summarize_call(self, context):
        """
        Call-center helper: produce type, priority, summary, suggested dispatch.
        Default: compose analyze_emergency + recommend_dispatch.
        """
        analysis = self.analyze_emergency(context)
        recommendation = self.recommend_dispatch(analysis, context)
        return {
            "analysis": analysis,
            "recommendation": recommendation,
            "emergency_type": analysis.get("gurmad_type") or analysis.get("category"),
            "priority": analysis.get("priority"),
            "summary": analysis.get("summary") or analysis.get("reason"),
            "suggested_dispatch": analysis.get("required_services"),
            "suggested_responders": {
                "hospital": recommendation.get("recommended_hospital"),
                "police": recommendation.get("recommended_police"),
                "fire": recommendation.get("recommended_fire"),
            },
            "reason": analysis.get("reason"),
            "confidence": analysis.get("confidence"),
        }

    def health_check(self):
        """Return True if the provider is usable."""
        return True
