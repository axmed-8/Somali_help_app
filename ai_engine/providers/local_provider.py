"""Local / on-prem model provider stub — implement without changing business logic."""
from ai_engine.providers.base import AIProvider


class LocalProvider(AIProvider):
    name = "local"

    def __init__(self, endpoint=None, model=None):
        self.endpoint = endpoint
        self.model = model or "local-emergency-model"

    def analyze_emergency(self, context):
        raise NotImplementedError(
            "Local AI provider is not configured. Set AI_PROVIDER=rule_based "
            "or implement LocalProvider.analyze_emergency."
        )

    def recommend_dispatch(self, analysis, context):
        raise NotImplementedError(
            "Local AI provider is not configured. Set AI_PROVIDER=rule_based "
            "or implement LocalProvider.recommend_dispatch."
        )

    def health_check(self):
        return bool(self.endpoint)
