# Call Center Emergency Dispatch — Implementation Report

**Feature:** Method 2 — Call Emergency Center  
**Date:** 2026-07-12  
**Compatibility:** Existing SOS (Method 1) and all dashboards preserved

---

## Summary

GurmadNet now supports **two parallel emergency methods**:

1. **Report Emergency** — existing citizen SOS form + auto-dispatch (unchanged)
2. **Call Emergency Center** — citizen dials the call center while GPS is silently sent to operators

A new role **`call_center`** has a dedicated login and live dispatch dashboard. Operators ask only *“What happened?”* (GPS/address already on screen), then dispatch to Hospital / Police / Fire with multi-dispatch support. Dispatch reuses `_auto_dispatch_emergency`, `hospital_logic.hospitals_by_distance`, escalation queues, notifications, and citizen tracking.

---

## New Files Created

| File | Purpose |
|------|---------|
| `call_center_logic.py` | Call CRUD, nearest responders, dispatch type resolution, stats |
| `templates/call_center_login.html` | Secure operator login (`/call-center/login`) |
| `templates/call_center_dashboard.html` | Live call queue + dispatch workspace |
| `templates/call_center_history.html` | Call history table |
| `static/js/call_center.js` | Operator UI (answer, types, dispatch, send GPS) |
| `static/css/call_center.css` | Call Center styles matching GurmadNet |
| `database/patch_call_center.sql` | MySQL ALTER for role enum + `call_center_calls` table |

---

## Existing Files Modified

| File | Changes |
|------|---------|
| `app.py` | Role `call_center`, settings/content defaults, seed operator, all Call Center routes/APIs, `_create_emergency_from_call` |
| `database/schema.sql` | `call_center` in users.role ENUM; `call_center_calls` table |
| `database/mysql_store.py` | Load/save `call_center_calls`; export/verify updates |
| `templates/index.html` | **Call Emergency Center** button (SOS unchanged) |
| `static/js/patient.js` | Silent GPS + initiate call + `tel:` dial |
| `templates/user_dashboard.html` | Link to Call Emergency Center |
| `templates/login.html` | Demo operator + link to Call Center login |
| `templates/admin_dashboard.html` | **Call Center** admin section |
| `templates/hospital_dashboard.html` | “CALL CENTER” badge on sourced alerts |
| `templates/police_dashboard.html` | CALL CENTER badge |
| `templates/fire_dashboard.html` | CALL CENTER badge |
| `README.md` | Roles, features, test account |
| `tests/test_ers.py` | Initiate + multi-dispatch + role guard tests |

---

## Database Changes

### Root cause of `Data truncated for column 'role'`

The live MySQL `users.role` ENUM was still:

```text
enum('citizen','hospital','police','fire','admin')
```

The Call Center feature inserts role=`call_center`. MySQL rejects (or truncates in non-strict modes) values outside the ENUM → **error 1265 Data truncated for column 'role'**.

The repo `schema.sql` / patch file had already been updated, but the **running database was never migrated**.

### Fix applied

1. `ALTER TABLE users MODIFY COLUMN role ENUM(...,'call_center')`
2. Created `call_center_calls` table
3. Added `users.last_seen_call_center` for operator heartbeat
4. `mysql_store.ensure_call_center_schema()` runs automatically when MySQL mode starts
5. Patch script: `python scripts/patch_call_center_mysql.py`

### New table: `call_center_calls`

Stores call sessions separately from emergencies:

- Caller identity, GPS, address, device_info  
- Operator assignment, status lifecycle  
- `emergency_types`, `dispatched_to`, `emergency_ids`  
- Timestamps, duration, final_status  

### Users

- Role ENUM extended with `call_center`  
- Optional field `last_seen_call_center` (JSON/MySQL payload) for operator online heartbeat  

### JSON mode

- Store key: `call_center_calls` → `database/call_center_calls.json`  

### MySQL migration

```bash
mysql -u root -p gurmad < database/patch_call_center.sql
```

---

## New Routes (Pages)

| Method | Path | Role | Description |
|--------|------|------|-------------|
| GET/POST | `/call-center/login` | Public (operators only on POST) | Operator login |
| GET | `/call-center` | `call_center` | Live dashboard |
| GET | `/call-center/history` | `call_center` | Call history page |

---

## New APIs

| Method | Path | Role | Description |
|--------|------|------|-------------|
| POST | `/api/call-center/initiate` | citizen | Silent GPS + return `tel:` href |
| GET | `/api/call-center/live` | call_center | Queue + stats + heartbeat |
| GET | `/api/call-center/history` | call_center, admin | History list |
| GET | `/api/call-center/calls/<id>` | call_center | Call detail + nearest |
| POST | `/api/call-center/calls/<id>/answer` | call_center | Answer incoming |
| POST | `/api/call-center/calls/<id>/status` | call_center | Update status |
| GET | `/api/call-center/calls/<id>/nearest` | call_center | Refresh nearest units |
| POST | `/api/call-center/calls/<id>/dispatch` | call_center | Multi-type dispatch |
| POST | `/api/call-center/calls/<id>/send-gps` | call_center | Send GPS to responders |
| POST | `/api/call-center/calls/<id>/cancel` | call_center | Cancel call |
| POST | `/api/call-center/calls/<id>/complete` | call_center | Complete call |
| GET | `/api/call-center/settings` | call_center, admin | Read CC settings |
| GET | `/api/admin/call-center/stats` | admin | Stats, operators, recent |
| POST | `/api/admin/call-center/settings` | admin | Update phones/priorities |

---

## Models / Data Shape

### Call record (logical model)

```text
id, user_id, caller_name, phone, latitude, longitude, address, district,
status (ringing|answered|in_progress|dispatched|completed|cancelled|missed),
operator_id, operator_name, emergency_type, emergency_types[],
dispatched_to[], emergency_ids[], nearest{}, device_info{},
notes, start_time, answered_at, dispatched_at, end_time, duration_sec,
final_status, source=call_center
```

### Emergency records from dispatch

Created via existing emergency pipeline with:

- `source: "call_center"`
- `call_id`
- `operator_id` / `operator_name`
- Standard tracking + hospital escalation for medical

---

## Security Changes

- New decorator `call_center_required` (role gate)
- Dedicated `/call-center/login` rejects non-`call_center` roles
- Self-signup cannot choose `call_center` or `admin`
- All Call Center APIs require authenticated operator (or admin where noted)
- Citizen initiate endpoint requires `citizen` role + Somalia GPS validation
- Main SOS and existing dashboards unchanged in authorization model

---

## Features Added

- Citizen **📞 Call Emergency Center** button (alongside SOS)
- Silent push of user ID, name, phone, GPS, address, time, device info
- Operator live incoming call cards
- Quick emergency types: Medical, Fire, Police, Accident, Family, Other
- Nearest hospital (existing `hospitals_by_distance`) + police/fire stations
- Actions: Call Hospital/Police/Fire, Send GPS, Dispatch, Transfer (info), Cancel, Complete
- Multi-dispatch (e.g. Medical + Police)
- Live stats: operators online, incoming, waiting, in progress, resolved today, avg response
- Call history page
- Admin **Call Center** menu: operators, phones, priorities, statistics
- Notifications to citizen, hospital, admins on dispatch
- Responder dashboards show CALL CENTER badge
- Operators are created by an administrator (role: `call_center`) — no demo accounts

---

## What Was NOT Changed (preserved)

- `/api/send_alert` SOS flow  
- Citizen SOS UI and type selection  
- Hospital accept/reject/escalation  
- Police / Fire dashboards  
- Existing tracking stages (Pending → … → Completed)  
- Chat, profile, admin user/emergency CRUD (extended only)  

---

## Testing Checklist

- [x] Existing pytest suite still passes  
- [x] `test_call_center_initiate_and_dispatch`  
- [x] `test_call_center_role_guard`  
- [ ] Manual: citizen taps Call Emergency Center → phone dial opens + call appears for operator  
- [ ] Manual: operator answers → sees GPS/address → never needs “where are you?”  
- [ ] Manual: multi-dispatch Medical + Police → both dashboards receive requests  
- [ ] Manual: Send GPS alone → hospital card with CALL CENTER badge  
- [ ] Manual: citizen dashboard tracks dispatched emergencies  
- [ ] Manual: admin Call Center settings save phones / enable toggle  
- [ ] Manual: existing SOS Report Emergency still works end-to-end  
- [ ] MySQL: run `patch_call_center.sql` then verify operator login  

### Quick manual commands

```bash
# JSON mode
set GURMADNET_DB=json
python app.py

# Operator — use a call_center account created by an admin
# http://127.0.0.1:5000/call-center/login

# Citizen — register with a real email, then sign in
# http://127.0.0.1:5000/login
# Home → Call Emergency Center
```

```bash
python -m pytest tests/ -v
```

---

## Architecture Notes

- Call sessions are **separate** from emergencies; dispatch **creates** normal emergencies so Hospital/Police/Fire UIs and citizen tracking keep working.
- Distance logic is **not duplicated** — `call_center_logic.find_nearest_responders` calls `hl.hospitals_by_distance` / `hl.haversine_km`.
- Phone call uses device `tel:` URI to `settings.call_center_phone` (configurable in Admin).
