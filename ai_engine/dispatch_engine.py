"""
Smart Dispatch AI — thin facade over AIEmergencyEngine.recommend.

Never dispatches. Returns ranked hospital / police / fire recommendations.
"""


def recommend_responders(engine, analysis, context):
    """Recommend best responders from analysis + live context (distance, capacity, ETA)."""
    return engine.recommend(analysis or {}, context or {})
