"""
Provider factory — switch AI backends via AI_PROVIDER without touching business logic.

Supported: rule_based (default), openai, gemini, claude, deepseek, local
"""
import os

from ai_engine.providers.rule_based import RuleBasedProvider

_PROVIDER_CACHE = {}


def list_providers():
    return ("rule_based", "openai", "gemini", "claude", "deepseek", "local")


def get_provider(name=None, **kwargs):
    """
    Return an AIProvider instance.

    name: override; else env AI_PROVIDER; else rule_based.
    Unknown names fall back to rule_based.
    """
    key = (name or os.environ.get("AI_PROVIDER") or "rule_based").strip().lower()
    if key not in list_providers():
        key = "rule_based"

    cache_key = (key, tuple(sorted(kwargs.items())))
    if cache_key in _PROVIDER_CACHE and not kwargs.get("force_new"):
        return _PROVIDER_CACHE[cache_key]

    if key == "rule_based":
        provider = RuleBasedProvider()
    elif key == "openai":
        from ai_engine.providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(
            api_key=kwargs.get("api_key") or os.environ.get("OPENAI_API_KEY"),
            model=kwargs.get("model") or os.environ.get("OPENAI_MODEL"),
        )
    elif key == "gemini":
        from ai_engine.providers.gemini_provider import GeminiProvider

        provider = GeminiProvider(
            api_key=kwargs.get("api_key") or os.environ.get("GEMINI_API_KEY"),
            model=kwargs.get("model") or os.environ.get("GEMINI_MODEL"),
        )
    elif key == "claude":
        from ai_engine.providers.claude_provider import ClaudeProvider

        provider = ClaudeProvider(
            api_key=kwargs.get("api_key") or os.environ.get("ANTHROPIC_API_KEY"),
            model=kwargs.get("model") or os.environ.get("CLAUDE_MODEL"),
        )
    elif key == "deepseek":
        from ai_engine.providers.deepseek_provider import DeepSeekProvider

        provider = DeepSeekProvider(
            api_key=kwargs.get("api_key") or os.environ.get("DEEPSEEK_API_KEY"),
            model=kwargs.get("model") or os.environ.get("DEEPSEEK_MODEL"),
        )
    elif key == "local":
        from ai_engine.providers.local_provider import LocalProvider

        provider = LocalProvider(
            endpoint=kwargs.get("endpoint") or os.environ.get("LOCAL_AI_ENDPOINT"),
            model=kwargs.get("model") or os.environ.get("LOCAL_AI_MODEL"),
        )
    else:
        provider = RuleBasedProvider()

    # If a cloud provider is selected but not healthy, fall back to rule_based
    if key != "rule_based" and not provider.health_check():
        provider = RuleBasedProvider()
        key = "rule_based"

    if not kwargs.get("force_new"):
        _PROVIDER_CACHE[(key, tuple(sorted({})))] = provider
    return provider


def clear_provider_cache():
    _PROVIDER_CACHE.clear()
