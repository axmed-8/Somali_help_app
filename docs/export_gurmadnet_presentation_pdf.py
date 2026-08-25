#!/usr/bin/env python3
"""Export GurmadNet V1 presentation to PDF — standalone, no app code changes."""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.lib.utils import simpleSplit

OUT = Path(__file__).resolve().parent / "GurmadNet_V1_Presentation.pdf"
W, H = landscape(A4)
M = 0.6 * inch

NAVY = colors.HexColor("#0B3A6E")
NAVY_D = colors.HexColor("#082A52")
ORANGE = colors.HexColor("#E8892F")
GRAY = colors.HexColor("#64748B")
TEXT = colors.HexColor("#0F172A")
LIGHT = colors.HexColor("#F4F6F9")


def draw_frame(c: canvas.Canvas, n: int, cover: bool = False):
    if cover:
        c.setFillColor(NAVY_D)
        c.rect(0, 0, W, H, fill=1, stroke=0)
        c.setFillColor(ORANGE)
        c.rect(0, H - 0.1 * inch, W, 0.1 * inch, fill=1, stroke=0)
        return
    c.setFillColor(LIGHT)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    c.setFillColor(NAVY)
    c.rect(0, H - 0.78 * inch, W, 0.78 * inch, fill=1, stroke=0)
    c.setFillColor(ORANGE)
    c.rect(0, H - 0.78 * inch, 0.14 * inch, 0.78 * inch, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(M, H - 0.52 * inch, "GURMADNET V1")
    c.setFont("Helvetica", 9)
    c.drawRightString(W - M, H - 0.52 * inch, f"Slide {n} of 15")
    c.setFillColor(colors.HexColor("#94A3B8"))
    c.setFont("Helvetica", 7.5)
    c.drawCentredString(W / 2, 0.26 * inch, "Somali Help App · Integrated Emergency Help Platform")


def wrap_lines(c, text, font, size, max_w):
    c.setFont(font, size)
    lines = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            lines.append("")
            continue
        lines.extend(simpleSplit(para, font, size, max_w))
    return lines


def draw_title(c, title, y):
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(M, y, title)
    c.setStrokeColor(ORANGE)
    c.setLineWidth(2)
    c.line(M, y - 8, M + 2.2 * inch, y - 8)
    return y - 36


def draw_bullets(c, items, y, size=12.5):
    c.setFillColor(TEXT)
    max_w = W - 2 * M - 0.2 * inch
    for item in items:
        lines = wrap_lines(c, item, "Helvetica", size, max_w - 16)
        c.setFont("Helvetica-Bold", size)
        c.drawString(M + 4, y, "•")
        c.setFont("Helvetica", size)
        for i, line in enumerate(lines):
            c.drawString(M + 18, y - i * (size + 4), line)
        y -= max(1, len(lines)) * (size + 4) + 4
    return y


def draw_note(c, note, y):
    if y < 0.65 * inch:
        y = 0.65 * inch
    c.setFillColor(GRAY)
    c.setFont("Helvetica-Oblique", 9)
    lines = wrap_lines(c, "Speaker note: " + note, "Helvetica-Oblique", 9, W - 2 * M)
    for line in lines:
        c.drawString(M, y, line)
        y -= 11
    return y


def draw_flow_boxes(c, labels, y):
    bw = (W - 2 * M - 5 * 6) / len(labels)
    x = M
    for lab in labels:
        c.setFillColor(colors.white)
        c.setStrokeColor(NAVY)
        c.setLineWidth(1)
        c.roundRect(x, y - 28, bw, 28, 4, fill=1, stroke=1)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(x + bw / 2, y - 18, lab)
        x += bw + 6
    return y - 40


def slide_cover(c):
    draw_frame(c, 1, cover=True)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 30)
    c.drawCentredString(W / 2, H * 0.58, "GurmadNet")
    c.setFont("Helvetica", 16)
    c.setFillColor(colors.HexColor("#E8F1FB"))
    c.drawCentredString(W / 2, H * 0.48, "Emergency Response & Intelligence Platform")
    c.setFont("Helvetica", 13)
    c.drawCentredString(W / 2, H * 0.40, "Somali Help App · Integrated Emergency Help Platform")
    c.drawCentredString(W / 2, H * 0.33, "University / Project Presentation")
    c.setFillColor(ORANGE)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(W / 2, H * 0.24, "V1 — Frozen & Verified · 137 Tests Passed")
    draw_note(c, "Introduce GurmadNet as one integrated emergency platform for Somalia.", 0.55 * inch)


def slide_content(c, n, title, bullets, note, extra=None):
    draw_frame(c, n)
    y = draw_title(c, title, H - 1.15 * inch)
    if extra:
        c.setFillColor(TEXT)
        c.setFont("Helvetica", 11)
        for line in wrap_lines(c, extra, "Helvetica", 11, W - 2 * M):
            c.drawString(M, y, line)
            y -= 14
        y -= 6
    y = draw_bullets(c, bullets, y)
    draw_note(c, note, max(0.55 * inch, y - 20))


def slide_table3(c, n, title, rows, note):
    draw_frame(c, n)
    y = draw_title(c, title, H - 1.15 * inch)
    col_w = (W - 2 * M) / 3
    headers, r1, r2 = rows
    for i, (h, a, b) in enumerate(zip(headers, r1, r2)):
        x = M + i * col_w
        c.setFillColor(NAVY)
        c.rect(x + 4, y - 22, col_w - 8, 22, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 11)
        c.drawCentredString(x + col_w / 2, y - 15, h)
        c.setFillColor(colors.white)
        c.setStrokeColor(colors.HexColor("#CBD5E1"))
        c.rect(x + 4, y - 22 - 52, col_w - 8, 52, fill=1, stroke=1)
        c.setFillColor(TEXT)
        c.setFont("Helvetica", 10)
        for j, txt in enumerate([a, b]):
            for k, ln in enumerate(wrap_lines(c, txt, "Helvetica", 10, col_w - 20)):
                c.drawString(x + 12, y - 38 - j * 18 - k * 12, ln)
    draw_note(c, note, 0.55 * inch)


def main():
    c = canvas.Canvas(str(OUT), pagesize=landscape(A4))
    c.setTitle("GurmadNet V1 Presentation")
    c.setAuthor("GurmadNet / Somali Help App")

    slide_cover(c)
    c.showPage()

    slides = [
        (2, "Introduction — What is GurmadNet?", [
            "Digital emergency platform built for Somalia",
            "Citizens report emergencies from a mobile phone",
            "Hospital, Police, Fire, and Call Center in one system",
            "GPS and maps support faster response",
            "AI recommends — humans approve and dispatch",
        ], "GurmadNet connects citizens and all emergency services in one place."),
        (3, "The Problem", [
            "Citizens may not reach the right service quickly",
            "Phone calls often lack clear location information",
            "Agencies often work in separate systems",
            "Duplicate reports waste responder time",
            "People near danger may not receive warnings",
        ], "Every second matters. Location and coordination are critical."),
        (4, "The Solution", [
            "One platform for citizens and responders",
            "GPS shared automatically with each report",
            "SOS app and Call Center voice in one system",
            "Connected Hospital, Police, and Fire desks",
            "Rule-based AI with required human approval",
        ], "One system, shared location, human control over every dispatch."),
    ]
    for n, t, b, note in slides:
        slide_content(c, n, t, b, note)
        c.showPage()

    # Slide 5 workflow
    draw_frame(c, 5)
    y = draw_title(c, "How It Works — Emergency Workflow", H - 1.15 * inch)
    c.setFillColor(TEXT)
    c.setFont("Helvetica", 11)
    c.drawString(M, y, "V1 end-to-end flow:")
    y -= 18
    flow = (
        "Citizen SOS or Call Center  →  Type + GPS  →  Route to responders  →  "
        "Accept & dispatch  →  Live tracking + chat  →  Completed"
    )
    for ln in wrap_lines(c, flow, "Helvetica", 10.5, W - 2 * M):
        c.drawString(M, y, ln)
        y -= 13
    y -= 8
    draw_flow_boxes(c, ["Report", "Locate", "Route", "Dispatch", "Track", "Close"], y)
    draw_note(c, "Walk through six steps from citizen report to case closure.", 0.55 * inch)
    c.showPage()

    slide_content(c, 6, "Citizen Features (V1)", [
        "Unified Home — SOS, Call Center, Medical, Police, Fire",
        "Emergency types including Qoys (family help)",
        "Automatic GPS capture and location status",
        "Live status, tracking, and in-app chat",
        "Map view and nearby hospitals / stations",
        "Profile, medical info, and password management",
    ], "Demo: citizen login at / — SOS or Call Emergency Center.")
    c.showPage()

    slide_table3(c, 7, "Hospital, Police & Fire (V1)",
        (["Hospital", "Police", "Fire"],
         ["Live queue & workspace", "Station desk", "Fire desk"],
         ["Accept, reject, assign ambulance", "Accept, dispatch, complete", "Accept, dispatch, complete"]),
        "Each service has its own command desk with accept-to-complete workflow.")
    c.showPage()

    for n, t, b, note in [
        (8, "Call Center Command Center (V1)", [
            "National operator desk at /call-center",
            "Live Emergency Queue with priority and GPS",
            "Voice calls via WebRTC (HTTPS or localhost)",
            "Operator asks: “What happened?” — not “Where are you?”",
            "Dispatch to Hospital, Police, and/or Fire",
            "AI Assistance panel — recommend only",
        ], "National coordination hub. Human decides every dispatch."),
        (9, "AI Emergency Intelligence (V1)", [
            "Active provider: rule_based (fast, offline, deterministic)",
            "Analysis: type, severity, priority, risk, confidence",
            "Recommends nearest hospital, police, and fire units",
            "Call Center: Analyze → Approve → Human Dispatch",
            "Cloud AI (OpenAI / DeepSeek) are stubs only",
            "AI never auto-dispatches — human approval required",
        ], "AI supports operators. It does not replace them."),
        (10, "Maps, GPS & Tracking (V1)", [
            "Browser GPS on citizen and Call Center flows",
            "Reverse geocoding for readable addresses",
            "Nearest hospitals, police stations, and fire stations",
            "Leaflet maps (+ optional Google Maps API key)",
            "Live tracking on citizen dashboard and status pages",
            "Driver GPS links for ambulance location updates",
        ], "Location is shared automatically with every report."),
        (11, "Admin & System Management (V1)", [
            "User accounts and role management (7 roles)",
            "Hospital, station, and Call Center facility setup",
            "System settings: timeouts, priorities, branding",
            "Audit log and platform content management",
            "Call Center statistics and operator management",
        ], "Administrators control access, facilities, and behaviour."),
        (12, "Technology Stack", [
            "Backend: Python 3, Flask, Flask-WTF, Flask-SocketIO",
            "Database: JSON (dev/test) + MySQL (production)",
            "Frontend: HTML, CSS, JavaScript",
            "Maps: Leaflet; optional Google Maps",
            "Voice: WebRTC + Socket.IO signaling",
            "AI V1: Rule-based engine · 137 pytest tests passing",
        ], "Proven open-source stack. Tests protect core workflows."),
    ]:
        slide_content(c, n, t, b, note)
        c.showPage()

    # Slide 13 status
    draw_frame(c, 13)
    y = draw_title(c, "V1 Status — What Works Today", H - 1.15 * inch)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(M, y, "Working now")
    y -= 16
    y = draw_bullets(c, [
        "Citizen SOS + Call Center",
        "Hospital / Police / Fire desks",
        "Call Center Command Center",
        "GPS, maps, tracking, chat, notifications",
        "Rule-based AI recommendations",
        "Auth, roles, admin, emergency lifecycle",
    ], y, size=11.5)
    c.setFillColor(ORANGE)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(M, y, "Limited in V1")
    y -= 16
    y = draw_bullets(c, [
        "Voice requires HTTPS or localhost",
        "Cloud AI not active in production",
        "Public demo URL needs manual HTTPS tunnel",
    ], y, size=11.5)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(W / 2, y - 10, "GURMADNET V1 — FROZEN · 137 tests passed")
    draw_note(c, "Demo what works. Mention HTTPS for voice if needed.", 0.55 * inch)
    c.showPage()

    # Slide 14 future
    draw_frame(c, 14)
    y = draw_title(c, "Future Development (Not in V1)", H - 1.15 * inch)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(M, y, "1. AI Crowd & Duplicate Incident Detection")
    y -= 18
    y = draw_bullets(c, [
        "Many citizens may report the same emergency",
        "50 Reports → AI Analysis → 1 Possible Incident",
        "Goal: reduce duplicate reports and improve coordination",
    ], y, size=11.5)
    y -= 6
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(M, y, "2. Smart Geo-Alert System")
    y -= 18
    y = draw_bullets(c, [
        "When an emergency is confirmed, alert people nearby",
        "Fire — Bakaaro → 1 KM Radius → Safety Alert",
        "Goal: protect people near dangerous areas",
    ], y, size=11.5)
    c.setFillColor(GRAY)
    c.setFont("Helvetica-Oblique", 10)
    c.drawString(M, y - 8, "These two features are planned — not built in V1.")
    draw_note(c, "Future only. Do not demo as working features.", 0.55 * inch)
    c.showPage()

    slide_content(c, 15, "Vision & Conclusion", [
        "A connected national emergency network for Somalia",
        "Fast, clear, and human-controlled response",
        "Today: GurmadNet V1 is frozen, tested, and presentation-ready",
        "Tomorrow: duplicate detection and public safety alerts",
        "Thank you — Questions welcome",
    ], "Offer a live demo: Citizen → Call Center → Hospital.")
    c.save()
    print(f"Wrote {OUT} ({OUT.stat().st_size // 1024} KB, 15 slides)")


if __name__ == "__main__":
    main()
