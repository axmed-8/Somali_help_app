"""Anthropic Claude provider stub — implement without changing business logic."""
from ai_engine.providers.base import AIProvider


class ClaudeProvider(AIProvider):
    name = "claude"

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key
        self.model = model or "claude-3-5-sonnet-latest"

    def analyze_emergency(self, context):
        raise NotImplementedError(
            "Claude provider is not configured. Set AI_PROVIDER=rule_based "
            "or implement ClaudeProvider.analyze_emergency."
        )

    def recommend_dispatch(self, analysis, context):
        raise NotImplementedError(
            "Claude provider is not configured. Set AI_PROVIDER=rule_based "
            "or implement ClaudeProvider.recommend_dispatch."
        )

    def health_check(self):
        return bool(self.api_key)
