# GurmadNet AI — JSON to MySQL Migration Report

**Date:** 2026-05-29  
**Database:** `gurmadnet_ai`  
**Storage driver:** PyMySQL (`database/mysql_store.py`)

## Summary

All application data storage has been migrated from JSON files to MySQL. The UI, routes, templates, dashboards, chat, notifications, and emergency workflows are unchanged. Storage is routed through existing `read_json()` / `save_json()` helpers, so `hospital_logic.py` continues to work without modification.

MySQL is enabled automatically when `database/db_config.env` exists. Tests force JSON mode via `GURMADNET_DB=json`.

---

## Created Files

| File | Purpose |
|------|---------|
| `database/connection.py` | Loads `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` from `db_config.env` |
| `database/db_config.env` | Database credentials (set your MySQL root password) |
| `database/schema.sql` | Full schema with PKs, FKs, indexes |
| `database/mysql_store.py` | Complete MySQL CRUD for all entities |
| `database/__init__.py` | Package marker |
| `scripts/init_mysql.py` | Creates database and tables |
| `scripts/migrate_json_to_mysql.py` | Imports all JSON data into MySQL |
| `MIGRATION_REPORT.md` | This report |

---

## Modified Files

| File | Changes |
|------|---------|
| `app.py` | Auto-detect MySQL; `read_json`/`save_json` route to MySQL; unified `load_users`/`save_users`/`load_emergencies`/`save_emergencies`; MySQL-aware admin backup |
| `tests/test_ers.py` | Forces `GURMADNET_DB=json` and reloads app module for isolated JSON tests |
| `requirements.txt` | Added `mysql-connector-python` (PyMySQL remains primary driver) |

**Unchanged (by design):** All templates, static assets, routes, `hospital_logic.py` business logic.

---

## MySQL Tables Created

| Table | Source JSON | Records to import |
|-------|-------------|---------------------|
| `hospitals` | `hospitals.json` | 9 |
| `users` | `users.json` | 9 |
| `emergencies` | `emergencies.json` | 4 |
| `notifications` | `notifications.json` | 276 |
| `messages` | `messages.json` | 10 |
| `announcements` | `announcements.json` | 2 |
| `settings` | `settings.json` | 1 (JSON blob) |
| `system_content` | `system_content.json` | 1 (JSON blob) |
| `audit_logs` | `audit_log.json` | 250 |

### Schema highlights

- **Primary keys:** `id` on all entity tables
- **Foreign keys:**
  - `users.hospital_id` → `hospitals.id`
  - `emergencies.user_id` → `users.id`
  - `emergencies.assigned_hospital_id` → `hospitals.id`
  - `notifications.request_id` → `emergencies.id`
  - `messages.request_id` → `emergencies.id` (CASCADE)
- **Indexes:** email, role, status, timestamps, notification targets, message request IDs
- **JSON columns:** `saved_locations`, `activity`, hospital services, emergency `payload`, settings/content blobs

---

## Manual Steps Required

### 1. Set MySQL password

Edit `database/db_config.env` and replace `YOUR_PASSWORD` with your MySQL root password:

```
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=gurmadnet_ai
DB_USER=root
DB_PASSWORD=<your_actual_password>
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

### 3. Initialize schema

```powershell
python scripts/init_mysql.py
```

### 4. Import existing JSON data

```powershell
python scripts/migrate_json_to_mysql.py
```

### 5. Start the application

```powershell
python app.py
```

The app auto-enables MySQL when `db_config.env` is present.

### 6. Verify functionality

| Feature | Test |
|---------|------|
| Login | `admin@emergency.so` / `admin123` |
| Registration | Sign up a new citizen account |
| Chat | Send message on an active emergency dashboard |
| Notifications | Trigger SOS and check notification panel |
| Emergency requests | SOS from citizen home or dashboard |
| Dashboard | `/dashboard` — map, ETA, timeline |
| Admin panel | `/admin` — users, stats, backup |

### 7. Run automated tests (JSON mode)

```powershell
$env:GURMADNET_DB="json"
python -m pytest tests/ -v
```

All **11 tests pass** in JSON mode.

---

## How Storage Routing Works

```
app.py read_json(path) / save_json(path)
        │
        ├── GURMADNET_DB=json  →  JSON files in database/
        │
        └── db_config.env exists  →  mysql_store.read_by_path / save_by_path
                                          │
                                          ├── users.json      → users table
                                          ├── hospitals.json  → hospitals table
                                          ├── emergencies.json → emergencies table
                                          ├── notifications.json → notifications table
                                          ├── messages.json   → messages table
                                          ├── announcements.json → announcements table
                                          ├── settings.json   → settings table
                                          ├── system_content.json → system_content table
                                          └── audit_log.json  → audit_logs table
```

`hospital_logic.py` receives `read_json` and `save_json` from `app.py` — no changes needed.

---

## Admin Backup (MySQL mode)

`POST /api/admin/backup` exports all tables as JSON snapshots into `database/backups/<timestamp>/`.

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `Access denied for user 'root'` | Update `DB_PASSWORD` in `db_config.env` |
| `Can't connect to MySQL server` | Start MySQL Server service (Windows Services) |
| `Unknown database` | Run `python scripts/init_mysql.py` |
| Tests fail with MySQL errors | Ensure `GURMADNET_DB=json` (test fixture sets this automatically) |
| Force JSON mode in production | Set environment variable `GURMADNET_DB=json` |

---

## Migration Status

| Step | Status |
|------|--------|
| Schema created | **Complete** |
| Storage layer implemented | **Complete** |
| App wired to MySQL | **Complete** |
| JSON data imported | **Complete** (verified 2026-06-10) |
| Runtime writes (SOS, chat, signup) | **Verified** |
| Automated tests | **11/11 passing** (JSON mode) |

### Imported record counts (verified)

| Table | JSON source | MySQL rows |
|-------|-------------|------------|
| hospitals | hospitals.json | 9 |
| users | users.json | 9 (+1 from verification signup → 10) |
| emergencies | emergencies.json | 4 (+1 from verification SOS → 5) |
| notifications | notifications.json | 276 (+4 from verification → 280) |
| messages | messages.json | 10 (+1 from verification → 11) |
| announcements | announcements.json | 2 |
| settings | settings.json | 1 |
| system_content | system_content.json | 1 |
| audit_logs | audit_log.json | 250 |

### Schema verification

- **9 tables** present in `gurmadnet_ai`
- **3 foreign keys** active: `users.hospital_id`, `emergencies.user_id`, `emergencies.assigned_hospital_id`
- **29 indexes** present
- **FK patch applied:** `notifications.request_id` and `messages.request_id` use indexes only (no FK) — required because notifications are created before emergencies are committed, and historical JSON contains orphaned `request_id` references

### Fixes applied during verification

1. **`begin_migration()` / `end_migration()`** — single connection with `FOREIGN_KEY_CHECKS=0` for bulk import (previous per-connection FK disable failed)
2. **`scripts/patch_mysql_schema.py`** — drops `fk_notifications_request` and `fk_messages_request` on existing databases

### Remaining JSON references (intentional)

| Location | Purpose |
|----------|---------|
| `database/*.json` | Original data archive; not read at runtime when MySQL is enabled |
| `read_json()` / `save_json()` in `app.py` | Compatibility wrappers that route to MySQL |
| `migrate_legacy_data()` | One-time copy from legacy `data/` folder |
| `POST /api/admin/backup` | Exports MySQL data to JSON snapshots |
| `database/db.py` | Unused legacy module (not imported by app) |
| `tests/test_ers.py` | Uses `GURMADNET_DB=json` with temp files |

**The live application runs fully on MySQL** when `database/db_config.env` is present.
