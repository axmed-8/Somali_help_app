"""
AI Emergency Engine service facade.

All GurmadNet code talks to this layer — never to a specific AI provider.
The AI never dispatches; it only analyzes and recommends.
"""
import logging
import threading

from ai_engine.factory import get_provider
from ai_engine.memory import AIMemory
from ai_engine import storage as store

logger = logging.getLogger(__name__)

_engine_singleton = None
_engine_lock = threading.Lock()


class AIEmergencyEngine:
    """
    Intelligence layer between emergency intake and human-approved dispatch.

    SOS path: analyze in parallel (non-blocking); never delay auto-dispatch.
    Call Center path: analyze + recommend for operator Approve/Reject/Manual.
    """

    def __init__(self, read_fn, save_fn, provider=None, provider_name=None):
        self.read_fn = read_fn
        self.save_fn = save_fn
        self.provider = provider or get_provider(provider_name)
        self.memory = AIMemory(read_fn, save_fn)

    @property
    def provider_name(self):
        return getattr(self.provider, "name", "unknown")

    def analyze(self, context):
        """Run decision analysis, persist, and write to AI Memory."""
        analysis = self.provider.analyze_emergency(context or {})
        analysis = store.save_analysis(analysis, self.read_fn, self.save_fn)
        self.memory.record_analysis(analysis)
        return analysis

    def recommend(self, analysis, context):
        """Run smart-dispatch recommendation, persist, and write to AI Memory."""
        recommendation = self.provider.recommend_dispatch(analysis or {}, context or {})
        if analysis and analysis.get("id"):
            recommendation["analysis_id"] = analysis["id"]
        recommendation = store.save_recommendation(
            recommendation, self.read_fn, self.save_fn
        )
        self.memory.record_recommendation(recommendation)
        return recommendation

    def analyze_and_recommend(self, context):
        """Full pipeline: Decision Engine → Smart Dispatch (still recommendation-only)."""
        analysis = self.analyze(context)
        recommendation = self.recommend(analysis, context)
        return {"analysis": analysis, "recommendation": recommendation}

    def summarize_call(self, context):
        """Call Center AI helper (provider may override; results are persisted)."""
        raw = self.provider.summarize_call(context or {})
        analysis = raw.get("analysis") or {}
        analysis = store.save_analysis(analysis, self.read_fn, self.save_fn)
        self.memory.record_analysis(analysis)
        recommendation = raw.get("recommendation") or {}
        recommendation["analysis_id"] = analysis.get("id")
        recommendation = store.save_recommendation(
            recommendation, self.read_fn, self.save_fn
        )
        self.memory.record_recommendation(recommendation)
        return {
            **raw,
            "analysis": analysis,
            "recommendation": recommendation,
        }

    def record_human_decision(self, decision):
        """Operator Approve / Reject / Manual — never auto-dispatch here."""
        rec_id = decision.get("recommendation_id")
        status_map = {
            "approve": "approved",
            "approved": "approved",
            "reject": "rejected",
            "rejected": "rejected",
            "manual": "manual",
        }
        human = (decision.get("decision") or "").lower()
        status = status_map.get(human)
        if rec_id and status:
            store.update_recommendation(
                rec_id,
                {
                    "status": status,
                    "human_decision": human,
                    "operator_id": decision.get("operator_id"),
                    "operator_name": decision.get("operator_name"),
                    "decision_notes": decision.get("notes", ""),
                },
                self.read_fn,
                self.save_fn,
            )
        return self.memory.record_human_decision(decision)

    def record_dispatch_result(self, result):
        return self.memory.record_dispatch_result(result)

    def record_outcome(self, outcome):
        return self.memory.record_outcome(outcome)

    def analyze_emergency_async(self, context, on_done=None):
        """
        Non-blocking analysis for SOS path.
        Dispatch must already have been started/completed by the caller.
        """
        context = dict(context or {})

        def _worker():
            try:
                result = self.analyze_and_recommend(context)
                if on_done:
                    on_done(result)
            except Exception:
                logger.exception(
                    "AI async analysis failed for emergency_id=%s",
                    context.get("emergency_id"),
                )

        thread = threading.Thread(target=_worker, name="ai-emergency-analyze", daemon=True)
        thread.start()
        return thread

    def get_latest_for_emergency(self, emergency_id):
        return {
            "analysis": store.get_analysis_for_emergency(emergency_id, self.read_fn),
            "recommendation": store.get_recommendation_for_emergency(
                emergency_id, self.read_fn
            ),
            "memory": self.memory.history_for_emergency(emergency_id),
        }

    def get_latest_for_call(self, call_id):
        return {
            "analysis": store.get_analysis_for_call(call_id, self.read_fn),
            "recommendation": store.get_recommendation_for_call(call_id, self.read_fn),
        }

    def stats(self):
        return store.memory_stats_today(self.read_fn)


def get_ai_engine(read_fn=None, save_fn=None, provider_name=None, reset=False):
    """
    Process-wide engine singleton.
    Pass read_fn/save_fn on first use (typically app.read_json / app.save_json).
    """
    global _engine_singleton
    with _engine_lock:
        if reset:
            _engine_singleton = None
        if _engine_singleton is None:
            if read_fn is None or save_fn is None:
                raise RuntimeError(
                    "get_ai_engine requires read_fn and save_fn on first initialization."
                )
            _engine_singleton = AIEmergencyEngine(
                read_fn, save_fn, provider_name=provider_name
            )
        return _engine_singleton
