"""Email provider factory — switch backends via EMAIL_PROVIDER env var."""
import os
import threading

from email_service.console_provider import ConsoleEmailProvider
from email_service.env_loader import load_email_env
from email_service.memory_provider import MemoryEmailProvider
from email_service.smtp_provider import SMTPEmailProvider

_lock = threading.Lock()
_cache = {}

_PROVIDERS = {
    "smtp": SMTPEmailProvider,
    "gmail": SMTPEmailProvider,
    "memory": MemoryEmailProvider,
    "console": ConsoleEmailProvider,
}


def list_email_providers():
    return sorted(_PROVIDERS.keys())


def clear_email_provider_cache():
    with _lock:
        _cache.clear()


def get_email_provider(name=None, force_new=False):
    load_email_env()
    key = (name or os.environ.get("EMAIL_PROVIDER") or "smtp").strip().lower()
    if key not in _PROVIDERS:
        key = "smtp"
    with _lock:
        if force_new or key not in _cache:
            _cache[key] = _PROVIDERS[key]()
        return _cache[key]
