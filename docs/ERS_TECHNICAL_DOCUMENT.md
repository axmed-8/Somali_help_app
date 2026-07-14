# Emergency Response System (ERS) — Technical Audit, Schema & Roadmap

**Project:** Emergency Help App — Somalia  
**Stack today:** Flask + vanilla JS + Leaflet + JSON files (`database/`)  
**Audit date:** 2026-05-29  
**Assumption:** Room-level (1–3 m) indoor precision requires additional hardware/floorplans; the current web MVP targets **district/GPS-level** accuracy with a path to indoor upgrades.

---

## 1. Executive Summary

| Area | Current state | Production readiness |
|------|---------------|----------------------|
| Auth (login, roles) | Working (citizen, hospital, police, fire, admin) | Good for MVP |
| JSON database | Working; **not** full ACID multi-process | Needs PostgreSQL for scale |
| Location (GPS + district) | Working (~10–50 m outdoor) | Not room-level |
| Indoor / UWB / BLE RTT | **Not implemented** | Requires native app + infrastructure |
| Responder navigation | Map modal + OSM tiles | No turn-by-turn indoor |
| Audit trail | **Added** (`audit_log.json`) | Migrate to DB table |
| Concurrent writes | **Mitigated** (atomic file replace + locks) | Use real DB transactions |

---

## 2. Audit Report — Issues Found & Fixes Applied

### 2.1 Database (JSON — primary store)

**Architecture:** Four files under `database/`:
- `users.json` — users + `next_id`
- `emergencies.json` — emergencies + `next_id`
- `system_content.json`, `settings.json`
- `audit_log.json` — **new** change history

| Issue | Severity | Status |
|-------|----------|--------|
| No referential integrity (`user_id` optional on emergencies) | High | **Fixed** — `user_id` stored on create |
| Race on concurrent `save_json` (lost updates) | High | **Fixed** — atomic write + per-file lock (`database/db.py`) |
| No audit trail | High | **Fixed** — `append_audit()` |
| Duplicate `data/` and `database/` folders | Medium | Migration on startup |
| No backup API | Medium | **Fixed** — `POST /api/admin/backup` |
| Orphaned location updates | Medium | **Fixed** — `location_history[]` on emergency |
| Not true ACID across servers | Critical (prod) | Documented — use PostgreSQL |

**JSON cannot provide:** foreign-key enforcement, multi-row transactions, isolation under heavy concurrency, or PostGIS queries. **Recommendation:** PostgreSQL 16 + PostGIS for production.

### 2.2 Backend & APIs

| Issue | Severity | Status |
|-------|----------|--------|
| API fetch without session cookies on dashboards | High | **Fixed** — `credentials: 'same-origin'` |
| `send_alert` missing precision metadata | Medium | **Fixed** — accuracy, method, confidence, building/floor/room |
| No live location updates endpoint | High | **Fixed** — `POST /api/emergencies/<id>/location` |
| No responder arrival states | Medium | **Fixed** — `responder_status` on emergency |
| No rate limiting on SOS | Low | Settings cap exists (`max_emergencies_per_day`) |
| SQL injection | N/A | JSON only; validate inputs |
| XSS | Medium | Templates escape via Jinja; avoid `innerHTML` with user data in admin JS |

### 2.3 Frontend

| Issue | Severity | Status |
|-------|----------|--------|
| Single GPS fix, no watch | High | **Fixed** — `watchPosition` + periodic POST |
| No offline queue | Medium | **Fixed** — `localStorage` pending alerts |
| No building/floor/room UI | Medium | **Fixed** — optional fields on location page |
| Hospital dashboard empty when not logged in | High | User must log in as hospital role |

### 2.4 Location accuracy (honest assessment)

**Current capability:**
- GPS/AGPS: ~5–30 m outdoors
- IP fallback: city/district level (~1–5 km)
- District name: nearest Mogadishu district by coordinates (~100–500 m)

**Not achievable in browser-only MVP without:**
- Calibrated indoor floorplans (GeoJSON)
- BLE beacons / Wi-Fi RTT fingerprinting
- Native mobile app (Expo/React Native) for background sensors
- UWB (iPhone/Android hardware dependent)

**Target for this codebase (MVP):** District + coordinates + optional building/floor/room **manual or future indoor fusion**.

---

## 3. Production Database Schema (PostgreSQL + PostGIS)

Use when migrating off JSON.

```sql
-- Extensions
CREATE EXTENSION IF NOT EXISTS postgis;

-- Users (victims, responders, admins)
CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) UNIQUE NOT NULL,
    name            VARCHAR(255) NOT NULL,
    phone           VARCHAR(32),
    password_hash   TEXT NOT NULL,
    role            VARCHAR(20) NOT NULL CHECK (role IN ('citizen','hospital','police','fire','admin','responder')),
    status          VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active','blocked')),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_login      TIMESTAMPTZ
);

-- Buildings & indoor maps
CREATE TABLE buildings (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    address     TEXT,
    centroid    GEOGRAPHY(POINT, 4326)
);

CREATE TABLE floors (
    id            SERIAL PRIMARY KEY,
    building_id   INTEGER NOT NULL REFERENCES buildings(id) ON DELETE CASCADE,
    level         INTEGER NOT NULL,
    floorplan_geo JSONB  -- GeoJSON polygons per room
);

CREATE TABLE rooms (
    id          SERIAL PRIMARY KEY,
    floor_id    INTEGER NOT NULL REFERENCES floors(id) ON DELETE CASCADE,
    name        VARCHAR(64) NOT NULL,  -- e.g. "304"
    polygon     GEOGRAPHY(POLYGON, 4326)
);

-- Emergencies
CREATE TABLE emergencies (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id),
    type            VARCHAR(32) NOT NULL,
    status          VARCHAR(32) DEFAULT 'pending',
    assigned_to     VARCHAR(32),  -- hospital | police | fire
    caller_name     VARCHAR(255),
    phone           VARCHAR(32),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    arrived_at      TIMESTAMPTZ,
    reached_victim_at TIMESTAMPTZ
);

-- Location history (one row per fix)
CREATE TABLE location_updates (
    id              SERIAL PRIMARY KEY,
    emergency_id    INTEGER NOT NULL REFERENCES emergencies(id) ON DELETE CASCADE,
    recorded_at     TIMESTAMPTZ DEFAULT NOW(),
    geom            GEOGRAPHY(POINT, 4326) NOT NULL,
    altitude_m      DOUBLE PRECISION,
    accuracy_m      DOUBLE PRECISION,
    method          VARCHAR(64),  -- gps, wifi_rtt, ble, fused
    confidence      SMALLINT CHECK (confidence BETWEEN 0 AND 100),
    building_id     INTEGER REFERENCES buildings(id),
    floor_level     INTEGER,
    room_id         INTEGER REFERENCES rooms(id),
    uncertainty_m   DOUBLE PRECISION
);

CREATE INDEX idx_location_emergency ON location_updates(emergency_id, recorded_at DESC);
CREATE INDEX idx_location_geom ON location_updates USING GIST(geom);

-- Responder assignments
CREATE TABLE responder_assignments (
    id              SERIAL PRIMARY KEY,
    emergency_id    INTEGER NOT NULL REFERENCES emergencies(id) ON DELETE CASCADE,
    responder_id    INTEGER REFERENCES users(id),
    unit_type       VARCHAR(32),  -- ambulance, police, fire
    status          VARCHAR(32) DEFAULT 'assigned',
    assigned_at     TIMESTAMPTZ DEFAULT NOW(),
    eta_minutes     INTEGER
);

CREATE TABLE navigation_logs (
    id              SERIAL PRIMARY KEY,
    assignment_id   INTEGER REFERENCES responder_assignments(id),
    step_index      INTEGER,
    instruction     TEXT,
    geom            GEOGRAPHY(LINESTRING, 4326),
    logged_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Audit trail
CREATE TABLE audit_log (
    id          SERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ DEFAULT NOW(),
    user_id     INTEGER REFERENCES users(id),
    action      VARCHAR(64) NOT NULL,
    entity_type VARCHAR(32),
    entity_id   INTEGER,
    details     JSONB
);
```

**ACID emergency + location insert (example):**

```python
with conn.transaction():
    cur.execute(
        "INSERT INTO emergencies (user_id, type, status, caller_name, phone) "
        "VALUES (%s,%s,'pending',%s,%s) RETURNING id",
        (user_id, etype, name, phone),
    )
    eid = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO location_updates (emergency_id, geom, accuracy_m, method, confidence) "
        "VALUES (%s, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, %s, %s, %s)",
        (eid, lng, lat, accuracy, method, confidence),
    )
```

---

## 4. API Design (current + extended)

### Auth
| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/login` | Session login |
| GET/POST | `/signup` | Register citizen/service |
| GET | `/logout` | End session |

### Citizen
| Method | Path | Body | Response |
|--------|------|------|----------|
| POST | `/api/send_alert` | See below | `{ success, id }` |
| POST | `/api/emergencies/<id>/location` | location fix | `{ success }` |

**POST `/api/send_alert`**
```json
{
  "type": "medical",
  "name": "Ahmed Ali",
  "phone": "0611111111",
  "latitude": 2.0469,
  "longitude": 45.3182,
  "district": "Wadajir District, Mogadishu",
  "building": "Hodan Medical Center",
  "floor": "3",
  "room": "304",
  "accuracy_m": 12.5,
  "altitude_m": 10.2,
  "method": "gps",
  "confidence": 78,
  "location": "Wadajir District (2.04690, 45.31820)"
}
```

### Responders (hospital / police / fire)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/get_emergencies?type=medical` | Filtered list + `location_history` |
| POST | `/api/update_status` | `{ id, status: "dispatched" }` |
| POST | `/api/emergencies/<id>/responder` | `{ action: "arrived_at_scene" \| "reached_victim" }` |
| GET | `/api/emergencies/<id>/route` | Outdoor route (OSRM) + victim coords |

### Admin
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/stats` | Dashboard metrics |
| POST | `/api/admin/backup` | JSON snapshot backup |

---

## 5. Code Examples

### 5.1 BLE trilateration (conceptual — native/mobile)

```python
import numpy as np

def trilaterate_2d(beacons, distances_m):
    """
    beacons: [(x,y), ...] at least 3
    distances_m: [d1, d2, d3]
    Returns (x, y) estimate
    """
    A, b = [], []
    x0, y0, d0 = beacons[0][0], beacons[0][1], distances_m[0]
    for i in range(1, len(beacons)):
        xi, yi = beacons[i]
        di = distances_m[i]
        A.append([2 * (xi - x0), 2 * (yi - y0)])
        b.append(d0**2 - di**2 - x0**2 + xi**2 - y0**2 + yi**2)
    pt, _, _, _ = np.linalg.lstsq(np.array(A), np.array(b), rcond=None)
    return float(pt[0]), float(pt[1])
```

### 5.2 Real-time location stream (WebSocket — production)

```javascript
// Client (responder)
const ws = new WebSocket(`wss://api.example.com/emergencies/${id}/live`);
ws.onmessage = (e) => {
  const fix = JSON.parse(e.data);
  EmergencyLocation.updateVictimMarker(fix.lat, fix.lng);
};
```

### 5.3 Indoor map matching (pseudo)

Match `(lat,lng)` or BLE position to nearest room polygon from floor GeoJSON.

---

## 6. Implementation Roadmap

| Phase | Duration | Deliverables |
|-------|----------|--------------|
| **0 — MVP (current)** | Done | JSON DB, GPS, dashboards, admin |
| **1 — Hardening** | 2–3 weeks | PostgreSQL migration, audit, backups, tests |
| **2 — Live tracking** | 3–4 weeks | WebSockets, `watchPosition`, responder ETA (OSRM) |
| **3 — Indoor v1** | 2–3 months | Floor GeoJSON for key Mogadishu buildings, manual room pick |
| **4 — Indoor v2** | 4–6 months | React Native app, BLE beacons, Wi-Fi fingerprint |
| **5 — Operations** | Ongoing | SMS/WhatsApp, 24/7 monitoring, SLA |

**Recommended stack (production):**
- DB: PostgreSQL 16 + PostGIS
- API: Flask or FastAPI + Gunicorn
- Real-time: Redis + WebSockets (Socket.IO)
- Maps outdoor: OSRM + Leaflet/Mapbox
- Maps indoor: Mapbox Indoor or custom GeoJSON
- Mobile: React Native + Expo (background location)

---

## 7. Testing Plan & Success Criteria

### 7.1 Automated (`tests/test_ers.py`)
- Login per role → correct redirect
- Citizen SOS → emergency in JSON
- Hospital API returns medical types only
- Blocked user cannot login
- Atomic write does not corrupt JSON

### 7.2 Location tests (realistic MVP)

| ID | Test | Success criteria |
|----|------|------------------|
| L1 | Outdoor GPS fix | Coords within 30 m of truth (open sky) |
| L2 | Deny GPS → IP fallback | Fix within 5 s, district shown |
| L3 | Location update stream | 2+ points in `location_history` within 60 s |
| L4 | Offline queue | Alert stored in localStorage, sent when online |

### 7.3 Production indoor (future)

| ID | Test | Success criteria |
|----|------|------------------|
| I1 | Room identification | 95% fixes within **3 m** of room centroid |
| I2 | Trigger latency | Room-level fix within **3 s** |
| I3 | Basement / no GPS | BLE/Wi-Fi fusion still under 5 m |

### 7.4 End-to-end scenario

1. Citizen logs in → SOS → Medical → allow GPS → enter Building/Floor/Room → Send  
2. Hospital logs in → sees alert on map within 5 s  
3. Accept → status `dispatched`  
4. Mark `arrived_at_scene` → `reached_victim`  
5. Admin exports CSV + audit log shows create/update  

---

## 8. Files Changed in This Audit Pass

- `database/db.py` — atomic JSON + audit helpers  
- `app.py` — integrated layer, new endpoints, richer location model  
- `static/js/location.js` — watch, offline queue  
- `templates/index.html` — building/floor/room, live updates  
- Dashboards — session cookies on fetch  
- `tests/test_ers.py` — automated tests  
- `docs/ERS_TECHNICAL_DOCUMENT.md` — this document  

---

## 9. Assumptions

1. Deployed first in Mogadishu with known districts; indoor room precision is phased.  
2. Responders use smartphone browsers with GPS.  
3. Victims use modern mobile browsers (HTTPS for geolocation).  
4. JSON MVP is single-server; multi-instance requires PostgreSQL.  
5. Turn-by-turn indoor navigation needs floorplans not yet loaded for Somalia sites.

---

*End of document*
