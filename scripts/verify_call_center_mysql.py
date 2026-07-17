"""Verify Call Center MySQL schema (no demo operator seeding)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.connection import reset_config

reset_config()

from database import mysql_store

print("available =", mysql_store.available())
assert mysql_store.available(), "pymysql / MySQL not available"
mysql_store.ensure_call_center_schema()
print("OK: call_center schema ensured (no demo users created)")
