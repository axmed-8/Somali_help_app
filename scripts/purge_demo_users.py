"""Remove all users except the sole production admin.

Keeps only: axmednuurc4@gmail.com (role=admin).
Run: python scripts/purge_demo_users.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)
os.environ["GURMADNET_DB"] = "mysql"

KEEP_EMAIL = "axmednuurc4@gmail.com"

from database import mysql_store


def main() -> int:
    if not mysql_store.available():
        print("MySQL unavailable")
        return 1

    with mysql_store._db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, email, role FROM users WHERE LOWER(email) = %s",
            (KEEP_EMAIL.lower(),),
        )
        row = cur.fetchone()
        if not row:
            print(f"ERROR: {KEEP_EMAIL} not found — aborting (refusing to wipe all users).")
            return 1

        cur.execute(
            "UPDATE users SET role = 'admin', status = 'active', "
            "username = %s WHERE id = %s",
            (KEEP_EMAIL.split("@")[0], row["id"]),
        )
        cur.execute("SELECT id, email, role FROM users")
        all_users = cur.fetchall()
        to_delete = [u for u in all_users if u["email"].lower() != KEEP_EMAIL.lower()]
        print(f"Keeping: {KEEP_EMAIL} (id={row['id']}) as admin")
        print(f"Deleting {len(to_delete)} other user(s):")
        for u in to_delete:
            print(f"  - id={u['id']} {u['email']} ({u['role']})")

        # Null out references that might block deletes (best-effort)
        for table, col in (
            ("emergencies", "user_id"),
            ("notifications", "user_id"),
            ("messages", "sender_id"),
            ("call_center_calls", "user_id"),
            ("call_center_calls", "operator_id"),
            ("hospitals", "owner_user_id"),
        ):
            try:
                cur.execute(
                    f"UPDATE {table} SET {col} = NULL "
                    f"WHERE {col} IS NOT NULL AND {col} <> %s",
                    (row["id"],),
                )
            except Exception as exc:
                print(f"  skip {table}.{col}: {exc}")

        cur.execute(
            "DELETE FROM users WHERE LOWER(email) <> %s",
            (KEEP_EMAIL.lower(),),
        )
        deleted = cur.rowcount
        conn.commit()
        print(f"Done. Deleted {deleted} user(s). Remaining:")
        cur.execute("SELECT id, email, role, status FROM users")
        for u in cur.fetchall():
            print(f"  id={u['id']} {u['email']} role={u['role']} status={u['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
