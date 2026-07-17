"""
AI Decision Engine — thin facade over AIEmergencyEngine.analyze.

Business code should prefer AIEmergencyEngine / get_ai_engine();
this module documents the Decision Engine boundary for the architecture.
"""


def analyze_emergency(engine, context):
    """Determine category, priority, risk, services, confidence, and reason."""
    return engine.analyze(context or {})
