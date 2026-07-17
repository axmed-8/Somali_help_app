"""Load SMTP/email settings from a local .env file into os.environ (never commits secrets)."""
import os

_LOADED = False


def load_email_env(base_dir=None):
    """
    Load project .env once. Safe if python-dotenv is missing or .env absent.
    Does not overwrite variables already set in the process environment.
    """
    global _LOADED
    if _LOADED:
        return False
    if base_dir is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(base_dir, ".env")
    try:
        from dotenv import load_dotenv
    except ImportError:
        _LOADED = True
        return False
    if os.path.isfile(env_path):
        load_dotenv(env_path, override=False)
    _LOADED = True
    return True


def reset_email_env_loader():
    """Test helper."""
    global _LOADED
    _LOADED = False
