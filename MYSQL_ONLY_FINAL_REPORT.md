# GurmadNet AI — MySQL-Only Storage Final Report

**Date:** 2026-06-10  
**Status:** MySQL is the only active production storage system

---

## Is the project fully using MySQL?

**YES**

---

## Verification Results

| Check | Result |
|-------|--------|
| `database/db_config.env` valid | **PASS** (`gurmad` / `sms_user@127.0.0.1:3306`) |
| MySQL connection | **PASS** |
| Login | **PASS** |
| Registration | **PASS** (+1 user in MySQL) |
| SOS submission | **PASS** (+1 emergency in MySQL) |
| Dashboard API | **PASS** |
| Data stored in MySQL tables | **PASS** (row counts increased after operations) |
| Automated tests | **PASS** (11/11 — test mode uses temp JSON via `GURMADNET_DB=json`) |

### MySQL row counts (after verification)

| Table | Rows |
|-------|------|
| hospitals | 9 |
| users | 11 |
| emergencies | 7 |
| notifications | 316 |
| messages | 10 |
| announcements | 2 |
| settings | 1 |
| system_content | 1 |
| audit_logs | 253 |

---

## Files Removed (permanently deleted)

### `database/json_archive/` (entire folder)

- `users.json`
- `hospitals.json`
- `emergencies.json`
- `notifications.json`
- `messages.json`
- `announcements.json`
- `settings.json`
- `system_content.json`
- `audit_log.json`

### Other removed files

| File | Reason |
|------|--------|
| `database/db.py` | Unused legacy JSON layer — not imported by app |
| `data/users.json` | Legacy copy — no longer used |
| `data/emergencies.json` | Legacy copy — no longer used |
| `data/settings.json` | Legacy copy — no longer used |
| `data/system_content.json` | Legacy copy — no longer used |

---

## Files Kept in `database/`

| File | Purpose |
|------|---------|
| `db_config.env` | MySQL credentials (**required**) |
| `connection.py` | Loads DB configuration |
| `mysql_store.py` | All MySQL read/write operations |
| `schema.sql` | Table definitions |
| `setup_gurmad_workbench.sql` | Database + user setup for Workbench |
| `__init__.py` | Package marker |

---

## Code Changes (JSON → MySQL entity keys)

| File | Change |
|------|--------|
| `app.py` | Replaced `*.json` path constants with entity keys (`users`, `emergencies`, etc.); added `read_store()` / `save_store()`; removed `migrate_legacy_data()` |
| `hospital_logic.py` | Uses `HOSPITALS_STORE`, `NOTIFICATIONS_STORE`, `MESSAGES_STORE` entity keys |
| `mysql_store.py` | Added `read_store()` / `save_store()` / `export_all()` with entity key mapping |
| `scripts/migrate_json_to_mysql.py` | Now requires `--source <dir>` for one-time JSON import (no longer reads from `database/`) |

---

## Remaining JSON Dependencies

| Dependency | Type | Production impact |
|------------|------|-------------------|
| `read_json()` / `save_json()` | Function aliases | Call `read_store()` / `save_store()` → MySQL when `USE_MYSQL=True` |
| `GURMADNET_DB=json` | Test env flag | Tests only — writes temp JSON in isolated temp directory |
| `POST /api/admin/backup` | Export feature | Writes JSON **snapshots** to `database/backups/` (export, not storage) |
| `scripts/migrate_json_to_mysql.py` | Recovery tool | Optional import from external `--source` directory |
| `*.json` in test temp dirs | Test isolation | Not used in production |

**No physical JSON data files are required in `database/` at runtime.**

---

## `database/` folder contents (final)

```
database/
  __init__.py
  connection.py
  db_config.env
  mysql_store.py
  schema.sql
  setup_gurmad_workbench.sql
```

No `.json` data files remain.
