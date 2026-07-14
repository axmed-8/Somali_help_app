"""Google Gemini provider stub — implement without changing business logic."""
from ai_engine.providers.base import AIProvider


class GeminiProvider(AIProvider):
    name = "gemini"

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key
        self.model = model or "gemini-1.5-flash"

    def analyze_emergency(self, context):
        raise NotImplementedError(
            "Gemini provider is not configured. Set AI_PROVIDER=rule_based "
            "or implement GeminiProvider.analyze_emergency."
        )

    def recommend_dispatch(self, analysis, context):
        raise NotImplementedError(
            "Gemini provider is not configured. Set AI_PROVIDER=rule_based "
            "or implement GeminiProvider.recommend_dispatch."
        )

    def health_check(self):
        return bool(self.api_key)
