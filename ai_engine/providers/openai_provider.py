"""OpenAI provider stub — implement without changing business logic."""
from ai_engine.providers.base import AIProvider


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key
        self.model = model or "gpt-4o-mini"

    def analyze_emergency(self, context):
        raise NotImplementedError(
            "OpenAI provider is not configured. Set AI_PROVIDER=rule_based "
            "or implement OpenAIProvider.analyze_emergency."
        )

    def recommend_dispatch(self, analysis, context):
        raise NotImplementedError(
            "OpenAI provider is not configured. Set AI_PROVIDER=rule_based "
            "or implement OpenAIProvider.recommend_dispatch."
        )

    def health_check(self):
        return bool(self.api_key)
