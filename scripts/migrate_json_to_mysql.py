#!/usr/bin/env python3
"""Import JSON backup files into MySQL (one-time / recovery tool).

Usage:
  python scripts/migrate_json_to_mysql.py --source path/to/json/backups
"""
import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from database import mysql_store  # noqa: E402


def _load_json(path, default):
    if not os.path.exists(path):
        return default, 0
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        for key in ("users", "emergencies", "hospitals", "notifications", "messages", "announcements", "entries"):
            if key in data:
                return data, len(data[key])
        return data, 1
    return data, 1


def main():
    parser = argparse.ArgumentParser(description="Import JSON backups into MySQL")
    parser.add_argument(
        "--source",
        default="",
        help="Directory containing JSON backup files (users.json, hospitals.json, etc.)",
    )
    args = parser.parse_args()

    if not mysql_store.available():
        print("Install PyMySQL: pip install PyMySQL")
        sys.exit(1)

    source = args.source or os.path.join(BASE, "database", "json_import")
    if not os.path.isdir(source):
        print(f"Source directory not found: {source}")
        print("Provide --source with a folder containing JSON backup files.")
        sys.exit(1)

    report = []
    migrations = [
        ("hospitals.json", mysql_store.save_hospitals, {"hospitals": [], "next_id": 1}),
        ("users.json", mysql_store.save_users, {"users": [], "next_id": 1}),
        ("emergencies.json", mysql_store.save_emergencies, {"emergencies": [], "next_id": 1}),
        ("notifications.json", mysql_store.save_notifications, {"notifications": [], "next_id": 1}),
        ("messages.json", mysql_store.save_messages, {"messages": [], "next_id": 1}),
        ("announcements.json", mysql_store.save_announcements, {"announcements": [], "next_id": 1}),
    ]

    mysql_store.begin_migration()
    try:
        for filename, save_fn, default in migrations:
            path = os.path.join(source, filename)
            data, count = _load_json(path, default)
            save_fn(data)
            report.append((filename.replace(".json", ""), count))
            print(f"Migrated {filename}: {count} records")

        for filename, save_fn, default in (
            ("settings.json", mysql_store.save_settings_dict, {}),
            ("system_content.json", mysql_store.save_content_dict, {}),
        ):
            path = os.path.join(source, filename)
            data, count = _load_json(path, default)
            save_fn(data)
            report.append((filename.replace(".json", ""), count))
            print(f"Migrated {filename}: {count} record(s)")

        audit_path = os.path.join(source, "audit_log.json")
        audit_data, audit_count = _load_json(audit_path, {"entries": [], "next_id": 1})
        mysql_store.save_audit_log(audit_data)
        report.append(("audit_logs", audit_count))
        print(f"Migrated audit_log.json: {audit_count} records")
    finally:
        mysql_store.end_migration()

    print("\nMigration complete.")
    counts = mysql_store.table_counts()
    for table, count in counts.items():
        print(f"  {table}: {count}")


if __name__ == "__main__":
    main()
