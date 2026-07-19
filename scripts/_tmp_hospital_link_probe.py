"""One-off probe: hospitals vs hospital-role users linkage."""
from database import mysql_store as m
from database.connection import get_connection

conn = get_connection()
try:
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, owner_user_id, contact_email FROM hospitals ORDER BY id")
        hospitals = cur.fetchall()
        cur.execute(
            "SELECT id, name, email, role, hospital_id, status FROM users "
            "WHERE role='hospital' ORDER BY id"
        )
        users = cur.fetchall()
    print("HOSPITALS:")
    for h in hospitals:
        print(dict(h) if hasattr(h, "keys") else h)
    print("HOSPITAL_USERS:")
    for u in users:
        print(dict(u) if hasattr(u, "keys") else u)
    # orphan analysis
    hids = {h["id"] if isinstance(h, dict) else h[0] for h in hospitals}
    for u in users:
        ud = dict(u) if hasattr(u, "keys") else None
        if ud:
            hid = ud.get("hospital_id")
            print(
                f"user {ud['id']}: hospital_id={hid} "
                f"facility_exists={hid in hids if hid else False}"
            )
finally:
    conn.close()
