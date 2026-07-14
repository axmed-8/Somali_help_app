#!/usr/bin/env python3
"""Apply schema patches to an existing GurmadNet AI MySQL database."""
import sys

BASE = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
sys.path.insert(0, BASE)

from database import mysql_store  # noqa: E402


def _drop_fk(cur, table, constraint):
    try:
        cur.execute(f"ALTER TABLE {table} DROP FOREIGN KEY {constraint}")
        print(f"Dropped FK {constraint} on {table}")
        return True
    except Exception as exc:
        if "1091" in str(exc) or "doesn't exist" in str(exc).lower():
            print(f"FK {constraint} on {table} already absent")
            return False
        raise


def main():
    if not mysql_store.available():
        print("Install PyMySQL: pip install PyMySQL")
        sys.exit(1)

    with mysql_store._db() as conn:
        with conn.cursor() as cur:
            _drop_fk(cur, "notifications", "fk_notifications_request")
            _drop_fk(cur, "messages", "fk_messages_request")
    print("Schema patch complete.")


if __name__ == "__main__":
    main()
