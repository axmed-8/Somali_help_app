"""Verify Call Center operator can be seeded into MySQL after ENUM fix."""
import os
import sys

# Force MySQL mode (unset json override)
os.environ.pop("GURMADNET_DB", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.connection import reset_config
reset_config()

import importlib
import app as ers

# Reload so USE_MYSQL re-evaluates
importlib.reload(ers)

print("USE_MYSQL =", ers.USE_MYSQL)
ers.seed_defaults()
udata = ers.load_users()
ops = [u for u in udata["users"] if u.get("role") == "call_center"]
print("call_center users:", [(u["id"], u["email"], u["role"]) for u in ops])
assert any(u["email"] == "operator@callcenter.so" for u in ops), "operator not seeded"
print("OK: operator seeded successfully")
