"""Render DB-status screenshot cards from lifecycle approval evidence."""
import json
import os
import textwrap

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# When run from tests/artifacts, ROOT above is wrong — resolve from this file path.
HERE = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.dirname(os.path.dirname(HERE)) if os.path.basename(HERE) == "artifacts" else os.path.dirname(HERE)
if os.path.basename(HERE) != "artifacts":
    APP_ROOT = os.path.dirname(HERE)

# Prefer explicit app root
APP_ROOT = r"c:\Users\hp\Documents\app"
EV_PATH = os.path.join(APP_ROOT, "tests", "artifacts", "lifecycle_approval_evidence.json")
OUT_DIR = os.path.join(APP_ROOT, "tests", "artifacts", "screenshots")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(EV_PATH, encoding="utf-8") as f:
        ev = json.load(f)

    try:
        font = ImageFont.truetype("arial.ttf", 16)
        font_b = ImageFont.truetype("arialbd.ttf", 20)
        font_s = ImageFont.truetype("arial.ttf", 13)
    except OSError:
        font = ImageFont.load_default()
        font_b = font
        font_s = font

    order = [
        "1_medical_complete",
        "2_all_hospitals_reject",
        "3_police_no_activity",
        "4_fire_never_accepted",
        "5_timeout",
        "6_google_maps",
        "7_dashboard_terminals",
    ]

    for sid in order:
        data = ev["scenarios"].get(sid)
        if not data:
            continue
        path = os.path.join(OUT_DIR, f"{sid}.png")
        draw_card(sid, data, path, font, font_b, font_s)
        print("wrote", path)


def draw_card(sid, data, path, font, font_b, font_s):
    W, H = 920, 640
    img = Image.new("RGB", (W, H), "#0f172a")
    d = ImageDraw.Draw(img)
    result = data.get("result", "?")
    color = "#16a34a" if result == "PASS" else "#dc2626"
    d.rectangle([0, 0, W, 64], fill="#1e293b")
    d.text((24, 18), f"Scenario {sid}: {data.get('title', '')}", fill="#f8fafc", font=font_b)
    d.rectangle([W - 140, 16, W - 24, 48], fill=color)
    d.text((W - 120, 22), result, fill="white", font=font_b)

    y = 84
    d.text((24, y), "Assertions", fill="#94a3b8", font=font_s)
    y += 24
    for a in data.get("assertions") or []:
        d.text((32, y), f"+ {a}", fill="#e2e8f0", font=font)
        y += 22

    y += 12
    d.text((24, y), "Database status", fill="#94a3b8", font=font_s)
    y += 24
    db = data.get("database_status")
    lines = []
    if isinstance(db, dict) and "status" in db:
        lines = [
            f"id={db.get('id')}  type={db.get('type')}  status={db.get('status')}",
            f"tracking_active={db.get('tracking_active')}  assigned_to={db.get('assigned_to')}",
            f"hospital_id={db.get('assigned_hospital_id')}  station_id={db.get('assigned_station_id')}",
            f"accepted_at={db.get('accepted_at')}  timestamp={db.get('timestamp')}",
        ]
        hist = db.get("status_history") or []
        lines.append(f"status_history ({len(hist)} events):")
        for h in hist[-8:]:
            lines.append(f"  - {h.get('timestamp')}: {h.get('status')} | {h.get('note')}")
    elif isinstance(db, dict):
        for k, v in list(db.items())[:12]:
            if isinstance(v, dict) and "status" in v:
                lines.append(f"{k}: status={v.get('status')} tracking={v.get('tracking_active')}")
            else:
                snippet = json.dumps(v, ensure_ascii=False)[:90]
                lines.append(f"{k}: {snippet}")

    for line in lines:
        for wrapped in textwrap.wrap(line, width=100) or [""]:
            if y > H - 40:
                break
            d.text((32, y), wrapped, fill="#cbd5e1", font=font_s)
            y += 18

    extra = data.get("extra") or {}
    if "dashboard_active_emergency" in extra:
        d.text(
            (24, H - 36),
            f"dashboard.active_emergency = {extra.get('dashboard_active_emergency')}",
            fill="#38bdf8",
            font=font,
        )

    img.save(path)


if __name__ == "__main__":
    main()
