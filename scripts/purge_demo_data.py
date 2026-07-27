"""Remove demo/test data from live MySQL. Keeps real accounts and hospitals.

Run: python scripts/purge_demo_data.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

os.environ.pop("TESTING", None)
os.environ["GURMADNET_DB"] = "mysql"

from database import mysql_store as m

KEEP_EMAILS = {
    "axmednuurc4@gmail.com",
}

DEMO_ANNOUNCEMENT_TITLES = {
    "Welcome to Somalia Emergency Response",
    "24/7 Emergency Hotline",
}

DEMO_SETTING_CLEAR = {
    "call_center_phone": "",
    "call_center_phone_secondary": "",
    "contact_phone": "",
    "emergency_hotline": "",
    "call_center_phone_primary": "",
}


def _is_demo_email(email: str) -> bool:
    e = (email or "").strip().lower()
    if e in KEEP_EMAILS:
        return False
    if e.endswith("@example.com") or e.endswith("@test.so") or e.endswith("@callcenter.so"):
        return True
    if "178437" in e:  # scripted ui.* test accounts
        return True
    if e.startswith("cit.") or e.startswith("citizen.only"):
        return True
    return False


def main() -> int:
    if not m.available():
        print("MySQL unavailable")
        return 1

    with m._db() as conn:
        cur = conn.cursor()

        cur.execute("SELECT id, email, role, name FROM users ORDER BY id")
        users = cur.fetchall()
        to_delete = [u for u in users if _is_demo_email(u["email"])]
        keep = [u for u in users if not _is_demo_email(u["email"])]
        print("Keeping users:")
        for u in keep:
            print(f"  id={u['id']} {u['email']} ({u['role']})")
        print(f"Deleting {len(to_delete)} demo/test user(s):")
        for u in to_delete:
            print(f"  id={u['id']} {u['email']} ({u['role']})")

        ids = [u["id"] for u in to_delete]
        if ids:
            ph = ", ".join(["%s"] * len(ids))
            for sql in (
                f"UPDATE hospitals SET owner_user_id = NULL WHERE owner_user_id IN ({ph})",
                f"UPDATE emergencies SET user_id = NULL WHERE user_id IN ({ph})",
                f"UPDATE messages SET sender_id = NULL WHERE sender_id IN ({ph})",
                f"UPDATE call_center_calls SET user_id = NULL WHERE user_id IN ({ph})",
                f"UPDATE call_center_calls SET operator_id = NULL WHERE operator_id IN ({ph})",
                f"UPDATE audit_logs SET user_id = NULL WHERE user_id IN ({ph})",
                f"DELETE FROM notifications WHERE target_id IN ({ph})",
                f"DELETE FROM users WHERE id IN ({ph})",
            ):
                try:
                    cur.execute(sql, ids)
                except Exception as exc:
                    print(f"  warn: {exc}")

        # Demo announcements
        cur.execute("SELECT id, title FROM announcements")
        for row in cur.fetchall():
            if (row.get("title") or "") in DEMO_ANNOUNCEMENT_TITLES:
                cur.execute("DELETE FROM announcements WHERE id = %s", (row["id"],))
                print(f"Deleted announcement: {row['title']}")

        # Clear demo phone numbers in settings (MySQL JSON payload)
        cur.execute("SELECT payload FROM settings WHERE id = 1")
        row = cur.fetchone()
        if row and row.get("payload") is not None:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            changed = False
            for key, val in DEMO_SETTING_CLEAR.items():
                if key in payload and payload.get(key) not in ("", None):
                    # clear known demo placeholders
                    old = str(payload.get(key) or "")
                    if "612000999" in old or "612000998" in old or "613910872" in old:
                        payload[key] = val
                        changed = True
                        print(f"Cleared settings.{key}")
            # also clear nested call_center block phones if present
            cc = payload.get("call_center")
            if isinstance(cc, dict):
                for k in ("phone_primary", "phone_secondary", "phone"):
                    old = str(cc.get(k) or "")
                    if "612000999" in old or "612000998" in old:
                        cc[k] = ""
                        changed = True
                        print(f"Cleared settings.call_center.{k}")
            if changed:
                cur.execute(
                    "UPDATE settings SET payload = %s WHERE id = 1",
                    (json.dumps(payload, ensure_ascii=False),),
                )

        conn.commit()
        print("Done. Remaining users:")
        cur.execute("SELECT id, email, role FROM users ORDER BY id")
        for u in cur.fetchall():
            print(f"  id={u['id']} {u['email']} ({u['role']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
