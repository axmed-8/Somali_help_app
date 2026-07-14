"""
GurmadNet AI Emergency Engine — provider-independent decision support.

Business logic must call only the service facade, never a specific provider.
The AI recommends; humans approve. SOS auto-dispatch is never delayed.
"""
from ai_engine.factory import get_provider, list_providers
from ai_engine.service import AIEmergencyEngine, get_ai_engine

__all__ = [
    "AIEmergencyEngine",
    "get_ai_engine",
    "get_provider",
    "list_providers",
]
