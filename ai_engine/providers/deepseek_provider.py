"""DeepSeek provider stub — implement without changing business logic."""
from ai_engine.providers.base import AIProvider


class DeepSeekProvider(AIProvider):
    name = "deepseek"

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key
        self.model = model or "deepseek-chat"

    def analyze_emergency(self, context):
        raise NotImplementedError(
            "DeepSeek provider is not configured. Set AI_PROVIDER=rule_based "
            "or implement DeepSeekProvider.analyze_emergency."
        )

    def recommend_dispatch(self, analysis, context):
        raise NotImplementedError(
            "DeepSeek provider is not configured. Set AI_PROVIDER=rule_based "
            "or implement DeepSeekProvider.recommend_dispatch."
        )

    def health_check(self):
        return bool(self.api_key)
