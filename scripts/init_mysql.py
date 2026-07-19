#!/usr/bin/env python3
"""Initialize GurmadNet AI MySQL database from schema.sql."""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from database.connection import load_config  # noqa: E402
from database import mysql_store  # noqa: E402


def _execute_schema(conn, schema_path):
    with open(schema_path, "r", encoding="utf-8") as f:
        sql = f.read()
    statements = []
    current = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current))
            current = []
    if current:
        statements.append("\n".join(current))
    with conn.cursor() as cur:
        for stmt in statements:
            s = stmt.strip()
            if not s:
                continue
            upper = s.upper()
            if upper.startswith("CREATE DATABASE") or upper.startswith("USE "):
                continue
            cur.execute(s)
    conn.commit()


def main():
    if not mysql_store.available():
        print("Install PyMySQL: pip install PyMySQL")
        sys.exit(1)

    import pymysql

    cfg = load_config()
    db_name = cfg["database"]
    cfg["cursorclass"] = pymysql.cursors.DictCursor
    conn = pymysql.connect(**cfg)
    schema_path = os.path.join(BASE, "database", "schema.sql")
    _execute_schema(conn, schema_path)
    conn.close()
    print(f"Schema applied. Database: {db_name}")

    # Idempotent indexes/FKs/orphan cleanup for both fresh and upgraded DBs
    integrity = mysql_store.ensure_production_integrity()
    print("Production integrity:", integrity)
    verify = mysql_store.verify_schema()
    print("Schema verify:", verify)
    print("Run scripts/migrate_json_to_mysql.py to import JSON data.")


if __name__ == "__main__":
    main()
