"""Inspect and patch MySQL for Call Center role + calls table."""
import sys
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from database.connection import load_config, reset_config
from database import mysql_store


ROLE_ENUM = (
    "ENUM('citizen','hospital','police','fire','admin','call_center') "
    "NOT NULL DEFAULT 'citizen'"
)

CREATE_CALLS = """
CREATE TABLE IF NOT EXISTS call_center_calls (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NULL,
  caller_name VARCHAR(120) NOT NULL,
  phone VARCHAR(40) DEFAULT '',
  latitude DOUBLE NULL,
  longitude DOUBLE NULL,
  address TEXT,
  district VARCHAR(120) DEFAULT '',
  status VARCHAR(40) NOT NULL DEFAULT 'ringing',
  operator_id INT NULL,
  operator_name VARCHAR(120) DEFAULT '',
  emergency_type VARCHAR(40) DEFAULT '',
  emergency_types JSON,
  dispatched_to JSON,
  emergency_ids JSON,
  nearest JSON,
  device_info JSON,
  notes TEXT,
  accuracy_m DOUBLE NULL,
  start_time DATETIME NOT NULL,
  answered_at DATETIME NULL,
  dispatched_at DATETIME NULL,
  end_time DATETIME NULL,
  duration_sec INT DEFAULT 0,
  final_status VARCHAR(40) DEFAULT '',
  source VARCHAR(40) DEFAULT 'call_center',
  INDEX idx_cc_status (status),
  INDEX idx_cc_operator (operator_id),
  INDEX idx_cc_user (user_id),
  INDEX idx_cc_start (start_time)
)
"""


def inspect():
    cfg = load_config()
    print("=== DB CONFIG ===")
    print(f"database={cfg['database']} user={cfg['user']} host={cfg['host']}")
    conn = mysql_store.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM users LIKE 'role'")
            role_col = cur.fetchone()
            print("=== users.role column ===")
            print(role_col)
            cur.execute("SHOW TABLES LIKE 'call_center_calls'")
            print("=== call_center_calls exists? ===", cur.fetchall())
            cur.execute("SHOW COLUMNS FROM users LIKE 'last_seen_call_center'")
            print("=== last_seen_call_center? ===", cur.fetchone())
            cur.execute("SELECT id, email, role, status FROM users ORDER BY id")
            print("=== users ===")
            for row in cur.fetchall():
                print(row)
    finally:
        conn.close()


def role_enum_has_call_center(type_str):
    return type_str and "call_center" in str(type_str).lower()


def apply_patch():
    print("\n=== APPLYING PATCH ===")
    conn = mysql_store.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM users LIKE 'role'")
            role_col = cur.fetchone()
            type_str = (role_col or {}).get("Type") or (role_col or {}).get("type") or ""
            print("Current role Type:", type_str)
            if not role_enum_has_call_center(type_str):
                sql = f"ALTER TABLE users MODIFY COLUMN role {ROLE_ENUM}"
                print("Running:", sql)
                cur.execute(sql)
                print("OK: role ENUM now includes call_center")
            else:
                print("OK: role ENUM already includes call_center")

            # Operator heartbeat column (safe additive)
            cur.execute("SHOW COLUMNS FROM users LIKE 'last_seen_call_center'")
            if not cur.fetchone():
                cur.execute(
                    "ALTER TABLE users ADD COLUMN last_seen_call_center DATETIME NULL "
                    "AFTER last_login"
                )
                print("OK: added users.last_seen_call_center")
            else:
                print("OK: last_seen_call_center already present")

            cur.execute(CREATE_CALLS)
            print("OK: call_center_calls table ensured")

            cur.execute("SHOW COLUMNS FROM users LIKE 'role'")
            print("Verified role:", cur.fetchone())
            cur.execute("SHOW TABLES LIKE 'call_center_calls'")
            print("Verified table:", cur.fetchall())
        conn.commit()
    finally:
        conn.close()
    print("=== PATCH COMPLETE ===")


if __name__ == "__main__":
    reset_config()
    if not mysql_store.available():
        print("PyMySQL not available")
        sys.exit(1)
    inspect()
    if "--inspect-only" in sys.argv:
        sys.exit(0)
    apply_patch()
    print("\n=== AFTER PATCH ===")
    inspect()
