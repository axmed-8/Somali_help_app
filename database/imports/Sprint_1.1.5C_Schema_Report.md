# Sprint 1.1.5C — Schema Report (source inspection only)

**Scope:** Read-only inspection of GurmadNet source. No database connection. No SQL executed.

**Primary sources:**
- `database/schema.sql`
- `database/migrations/20260720_facility_registries.sql`
- `database/mysql_store.py` (`load_hospitals` / `save_hospitals` / `load_response_stations` / `save_response_stations`)
- `hospital_logic.py`, `facility_registry.py`, `admin_registry_api.py`

**Master data source (values only):** Sprint 1.1.5 Master Data Report v2 (Downloads).

---

## 1. Tables used for facilities

| Role | Actual table | Notes |
|------|----------------|-------|
| Hospitals | `hospitals` | Dedicated facility table |
| Police stations | `response_stations` | Filtered by `kind = 'police'` |
| Fire stations | `response_stations` | Filtered by `kind = 'fire'` |

There are **no** `police_stations` or `fire_stations` tables in this project.

Related (not part of this master-data import):
- `ambulance_units` — fleet rows owned by a hospital (`hospital_id`)
- `call_centers` — separate facility registry

---

## 2. `hospitals` columns (from `schema.sql`)

| Column | Type | Null / default | Import notes |
|--------|------|----------------|--------------|
| `id` | INT PK AI | — | Do not hardcode; let AUTO_INCREMENT assign on insert |
| `name` | VARCHAR(180) NOT NULL | — | Required; app also enforces case-insensitive uniqueness |
| `city` | VARCHAR(80) | `''` | Use `Mogadishu` |
| `region` | VARCHAR(80) | `''` | Use `Banadir` |
| `district` | VARCHAR(80) | `''` | From master data |
| `address` | VARCHAR(255) | `''` | From master data |
| `latitude` | DOUBLE NOT NULL | — | Required |
| `longitude` | DOUBLE NOT NULL | — | Required |
| `phone` | VARCHAR(40) | `''` | Emergency phone (normalized) |
| `emergency_contacts` | JSON | NULL | Array; primary emergency phone |
| `services` | JSON | NULL | Array of service strings |
| `specialties` | JSON | NULL | Mirror `services` for app compatibility |
| `ambulance_available` | TINYINT(1) | 0 | Set when master data confirms ambulance |
| `ambulance_count` | INT | 0 | |
| `emergency_capacity` | INT | 10 | Default kept unless known |
| `rating` | DOUBLE | 4.0 | Default kept |
| `operating_status` | ENUM('open','limited','closed') | `'open'` | |
| `contact_email` | VARCHAR(180) | `''` | Leave empty (not in master data) |
| `owner_user_id` | INT NULL | NULL | Leave NULL on insert; preserve on update |
| `location_verified` | TINYINT(1) | 0 | 1 when report confidence is High |
| `logo_url` | VARCHAR(255) | `''` | Leave empty |
| `created_at` | DATETIME NOT NULL | — | Required on insert |
| `updated_at` | DATETIME NOT NULL | — | Required on insert/update |

**Indexes:** `region`, `city`, `operating_status`, `owner_user_id`. No UNIQUE on `name` at DB level (app-level check only).

---

## 3. `response_stations` columns

| Column | Type | Null / default | Import notes |
|--------|------|----------------|--------------|
| `id` | INT PK AI | — | Do not hardcode on insert |
| `kind` | ENUM('police','fire') NOT NULL | — | Discriminator for police vs fire |
| `name` | VARCHAR(180) NOT NULL | — | Required |
| `city` | VARCHAR(80) | `''` | `Mogadishu` |
| `region` | VARCHAR(80) | `''` | `Banadir` |
| `district` | VARCHAR(80) | `''` | From master data (or empty if unverified) |
| `address` | VARCHAR(255) | `''` | |
| `latitude` | DOUBLE NULL | NULL | Present for all master rows (needed for maps/dispatch) |
| `longitude` | DOUBLE NULL | NULL | |
| `phone` | VARCHAR(40) | `''` | Empty string when unverified |
| `operating_status` | ENUM('open','limited','closed') | `'open'` | |
| `owner_user_id` | INT NULL | NULL | Leave NULL |
| `created_at` | DATETIME NOT NULL | — | Required |
| `updated_at` | DATETIME NOT NULL | — | Required |

---

## 4. Relationships

```
hospitals.id  <── users.hospital_id                 (FK ON DELETE SET NULL)
hospitals.id  <── emergencies.assigned_hospital_id  (FK ON DELETE SET NULL)
hospitals.id  <── ambulance_units.hospital_id       (FK ON DELETE CASCADE)
hospitals.owner_user_id ──> users.id                (deferred FK via ensure_production_integrity)

response_stations.id  <── users.station_id                 (deferred FK)
response_stations.id  <── emergencies.assigned_station_id  (deferred FK)
response_stations.owner_user_id ──> users.id               (deferred FK)
```

**Implication for import:** Prefer upsert-by-name. Avoid deleting hospital/station rows that may be referenced by users, emergencies, or ambulances.

---

## 5. Field mapping (master data → schema)

| Master field | Target |
|--------------|--------|
| Official Name | `hospitals.name` / `response_stations.name` |
| District | `district` |
| Full Address | `address` |
| Latitude / Longitude | `latitude` / `longitude` |
| Emergency Phone | `phone` (+ `emergency_contacts` JSON for hospitals) |
| General Phone | omitted (no column); Banadir admin lines not stored |
| Emergency Services | `services` + `specialties` JSON arrays |
| verification_source / data_confidence | **no columns** — not imported |
| Police / Fire facility type | `response_stations.kind` |

---

## 6. Import script location

`database/imports/Sprint_1.1.5C_Facilities_Import.sql`

Strategy:
1. `UPDATE` existing hospitals matched by known name aliases (e.g. `banadir` ↔ Banadir Hospital).
2. `INSERT … SELECT … WHERE NOT EXISTS` for hospitals/stations that are not already present (case-insensitive name).
3. Does **not** delete placeholder/demo stations; optional close statements are commented for manual choice.
4. Does **not** `COMMIT` automatically — ends with review queries; you commit or rollback.

---

## 7. Verification Checklist

Use after you manually run the SQL (WorkBench / mysql client).

### Pre-flight
- [ ] Full `mysqldump` backup completed
- [ ] Confirmed target database name (`gurmad` or your live DB)
- [ ] Confirmed tables `hospitals` and `response_stations` exist (`SHOW TABLES`)
- [ ] Reviewed script; no `DROP TABLE` / wipe statements present

### After import (still in transaction or after COMMIT)
- [ ] Hospital count includes the 5 master names (plus any pre-existing extras you kept):
  ```sql
  SELECT id, name, district, latitude, longitude, phone, operating_status, location_verified
  FROM hospitals ORDER BY id;
  ```
- [ ] No duplicate Banadir / Madina rows (same facility twice under different spellings)
- [ ] Police rows present (`kind='police'`), expect 4 new names if none existed:
  ```sql
  SELECT id, kind, name, district, latitude, longitude, phone
  FROM response_stations WHERE kind='police' ORDER BY id;
  ```
- [ ] Fire rows present (`kind='fire'`), expect 1 master fire facility:
  ```sql
  SELECT id, kind, name, latitude, longitude, phone
  FROM response_stations WHERE kind='fire' ORDER BY id;
  ```
- [ ] All imported hospitals have non-NULL lat/lng
- [ ] All imported stations used for maps have non-NULL lat/lng
- [ ] `owner_user_id` on updated hospitals unchanged if staff was already linked

### App checks (manual, after COMMIT + server restart if needed)
- [ ] Admin → Hospitals Management lists imported hospitals
- [ ] Admin → Stations lists police + fire with correct `kind`
- [ ] Admin / command live map shows markers at imported coordinates
- [ ] Medical SOS / dispatch can assign a nearest hospital from the imported set
- [ ] Police / fire desk can see stations (claim/dispatch paths that use `response_stations`)
- [ ] Existing linked hospital user accounts still resolve (`users.hospital_id`)

### Rollback
- [ ] If anything looks wrong before COMMIT: `ROLLBACK;`
- [ ] If already committed: restore from the pre-flight dump
