-- =====================================================================
-- GurmadNet Sprint 1.1.5C — Master Data Import (schema-matched)
-- =====================================================================
-- Generated from project source (database/schema.sql + facility modules).
-- Values from Sprint 1.1.5 Master Data Report v2.
--
-- STATUS: PREPARED ONLY. NOT EXECUTED BY THE AGENT.
-- Review in Workbench / mysql client, then run yourself.
--
-- Targets REAL tables:
--   hospitals
--   response_stations  (kind = 'police' | 'fire')
--
-- Safe strategy:
--   1) UPDATE at most ONE existing hospital row per facility (lowest id /
--      prefer canonical name) — avoids renaming two aliases to the same name
--   2) INSERT only when no alias already exists (case-insensitive)
--   3) Never deletes facilities (preserves FKs)
--   4) Never writes owner_user_id on UPDATE; INSERT uses NULL
--
-- MySQL 8.0: uses JSON_ARRAY(), UTC_TIMESTAMP(), START TRANSACTION
-- Encoding: SET NAMES utf8mb4 (ASCII-safe facility names; no combining marks)
--
-- Recommended:
--   mysqldump backup FIRST
--   run whole script
--   review SELECT checks
--   COMMIT or ROLLBACK manually
-- =====================================================================

SET NAMES utf8mb4;
SET CHARACTER SET utf8mb4;

START TRANSACTION;

-- ---------------------------------------------------------------------
-- HOSPITALS — update at most one matching row per facility
-- ---------------------------------------------------------------------

UPDATE hospitals h
INNER JOIN (
  SELECT id FROM hospitals
  WHERE LOWER(TRIM(name)) IN (
    'banadir',
    'banadir hospital',
    'banadir hospital (banadir maternity & children''s hospital)',
    'banadir maternity & children''s hospital'
  )
     OR LOWER(TRIM(name)) LIKE 'banadir hospital%'
  ORDER BY
    CASE WHEN LOWER(TRIM(name)) = 'banadir hospital' THEN 0 ELSE 1 END,
    id ASC
  LIMIT 1
) pick ON pick.id = h.id
SET
  h.name = 'Banadir Hospital',
  h.city = 'Mogadishu',
  h.region = 'Banadir',
  h.district = 'Wadajir',
  h.address = 'Jidka Afgooye, Mogadishu',
  h.latitude = 2.0347553,
  h.longitude = 45.2991468,
  h.phone = '+252618035555',
  h.emergency_contacts = JSON_ARRAY('+252618035555', '+252615334486'),
  h.services = JSON_ARRAY(
    'Emergency department',
    'Maternal care',
    'Pediatrics',
    '24/7 emergency'
  ),
  h.specialties = JSON_ARRAY(
    'Emergency department',
    'Maternal care',
    'Pediatrics',
    '24/7 emergency'
  ),
  h.ambulance_available = 0,
  h.location_verified = 1,
  h.operating_status = 'open',
  h.updated_at = UTC_TIMESTAMP();

UPDATE hospitals h
INNER JOIN (
  SELECT id FROM hospitals
  WHERE LOWER(TRIM(name)) IN (
    'madina',
    'medina',
    'madina hospital',
    'medina hospital'
  )
     OR LOWER(TRIM(name)) LIKE 'madina hospital%'
     OR LOWER(TRIM(name)) LIKE 'medina hospital%'
  ORDER BY
    CASE WHEN LOWER(TRIM(name)) = 'madina hospital' THEN 0 ELSE 1 END,
    id ASC
  LIMIT 1
) pick ON pick.id = h.id
SET
  h.name = 'Madina Hospital',
  h.city = 'Mogadishu',
  h.region = 'Banadir',
  h.district = 'Wadajir',
  h.address = 'Wadajir District, Mogadishu (exact street address unverified)',
  h.latitude = 2.0292865,
  h.longitude = 45.2979802,
  h.phone = '+252616977388',
  h.emergency_contacts = JSON_ARRAY('+252616977388'),
  h.services = JSON_ARRAY(
    'Trauma',
    'Maternal emergency',
    'Emergency department'
  ),
  h.specialties = JSON_ARRAY(
    'Trauma',
    'Maternal emergency',
    'Emergency department'
  ),
  h.ambulance_available = 0,
  h.location_verified = 0,
  h.operating_status = 'open',
  h.updated_at = UTC_TIMESTAMP();

UPDATE hospitals h
INNER JOIN (
  SELECT id FROM hospitals
  WHERE LOWER(TRIM(name)) IN (
    'de martino public hospital',
    'de martino hospital',
    'demartino hospital'
  )
     OR LOWER(TRIM(name)) LIKE 'de martino %'
     OR LOWER(TRIM(name)) LIKE 'de martino'
  ORDER BY
    CASE WHEN LOWER(TRIM(name)) = 'de martino public hospital' THEN 0 ELSE 1 END,
    id ASC
  LIMIT 1
) pick ON pick.id = h.id
SET
  h.name = 'De Martino Public Hospital',
  h.city = 'Mogadishu',
  h.region = 'Banadir',
  h.district = 'Hamar Jajab',
  h.address = 'Via Londra Road, Hamar Jajab, Mogadishu',
  h.latitude = 2.0297769,
  h.longitude = 45.3386345,
  h.phone = '+252615572605',
  h.emergency_contacts = JSON_ARRAY('+252615572605'),
  h.services = JSON_ARRAY(
    'Emergency department',
    'Day care',
    'ICU'
  ),
  h.specialties = JSON_ARRAY(
    'Emergency department',
    'Day care',
    'ICU'
  ),
  h.ambulance_available = 0,
  h.location_verified = 1,
  h.operating_status = 'open',
  h.updated_at = UTC_TIMESTAMP();

UPDATE hospitals h
INNER JOIN (
  SELECT id FROM hospitals
  WHERE LOWER(TRIM(name)) IN (
    'erdogan hospital (digfer)',
    'erdogan hospital',
    'digfer hospital'
  )
     OR LOWER(TRIM(name)) LIKE 'erdogan hospital%'
     OR LOWER(TRIM(name)) LIKE 'erdo_an hospital%'
  ORDER BY
    CASE WHEN LOWER(TRIM(name)) = 'erdogan hospital (digfer)' THEN 0 ELSE 1 END,
    id ASC
  LIMIT 1
) pick ON pick.id = h.id
SET
  h.name = 'Erdogan Hospital (Digfer)',
  h.city = 'Mogadishu',
  h.region = 'Banadir',
  h.district = 'Hodan',
  h.address = 'Digfer area, Hodan District, Mogadishu',
  h.latitude = 2.0437655,
  h.longitude = 45.3055247,
  h.phone = '+252613773333',
  h.emergency_contacts = JSON_ARRAY('+252613773333'),
  h.services = JSON_ARRAY(
    'Emergency department',
    'ICU',
    'Surgery',
    'Teaching hospital'
  ),
  h.specialties = JSON_ARRAY(
    'Emergency department',
    'ICU',
    'Surgery',
    'Teaching hospital'
  ),
  h.ambulance_available = 0,
  h.location_verified = 1,
  h.operating_status = 'open',
  h.updated_at = UTC_TIMESTAMP();

UPDATE hospitals h
INNER JOIN (
  SELECT id FROM hospitals
  WHERE LOWER(TRIM(name)) IN ('wadajir hospital')
     OR LOWER(TRIM(name)) LIKE 'wadajir hospital%'
  ORDER BY
    CASE WHEN LOWER(TRIM(name)) = 'wadajir hospital' THEN 0 ELSE 1 END,
    id ASC
  LIMIT 1
) pick ON pick.id = h.id
SET
  h.name = 'Wadajir Hospital',
  h.city = 'Mogadishu',
  h.region = 'Banadir',
  h.district = 'Wadajir',
  h.address = 'Jaale Siyaad Road, Mogadishu',
  h.latitude = 2.0206158,
  h.longitude = 45.2913877,
  h.phone = '+252610598888',
  h.emergency_contacts = JSON_ARRAY('+252610598888'),
  h.services = JSON_ARRAY(
    'Emergency department',
    'ICU',
    'Ambulance transport'
  ),
  h.specialties = JSON_ARRAY(
    'Emergency department',
    'ICU',
    'Ambulance transport'
  ),
  h.ambulance_available = 1,
  h.ambulance_count = GREATEST(COALESCE(h.ambulance_count, 0), 1),
  h.location_verified = 1,
  h.operating_status = 'open',
  h.updated_at = UTC_TIMESTAMP();

-- ---------------------------------------------------------------------
-- HOSPITALS — insert when missing
-- ---------------------------------------------------------------------

INSERT INTO hospitals (
  name, city, region, district, address,
  latitude, longitude, phone,
  emergency_contacts, services, specialties,
  ambulance_available, ambulance_count, emergency_capacity, rating,
  operating_status, contact_email, owner_user_id, location_verified, logo_url,
  created_at, updated_at
)
SELECT
  'Banadir Hospital', 'Mogadishu', 'Banadir', 'Wadajir', 'Jidka Afgooye, Mogadishu',
  2.0347553, 45.2991468, '+252618035555',
  JSON_ARRAY('+252618035555', '+252615334486'),
  JSON_ARRAY('Emergency department', 'Maternal care', 'Pediatrics', '24/7 emergency'),
  JSON_ARRAY('Emergency department', 'Maternal care', 'Pediatrics', '24/7 emergency'),
  0, 0, 10, 4.0,
  'open', '', NULL, 1, '',
  UTC_TIMESTAMP(), UTC_TIMESTAMP()
WHERE NOT EXISTS (
  SELECT 1 FROM hospitals h
  WHERE LOWER(TRIM(h.name)) IN (
    'banadir', 'banadir hospital',
    'banadir hospital (banadir maternity & children''s hospital)',
    'banadir maternity & children''s hospital'
  )
     OR LOWER(TRIM(h.name)) LIKE 'banadir hospital%'
);

INSERT INTO hospitals (
  name, city, region, district, address,
  latitude, longitude, phone,
  emergency_contacts, services, specialties,
  ambulance_available, ambulance_count, emergency_capacity, rating,
  operating_status, contact_email, owner_user_id, location_verified, logo_url,
  created_at, updated_at
)
SELECT
  'Madina Hospital', 'Mogadishu', 'Banadir', 'Wadajir',
  'Wadajir District, Mogadishu (exact street address unverified)',
  2.0292865, 45.2979802, '+252616977388',
  JSON_ARRAY('+252616977388'),
  JSON_ARRAY('Trauma', 'Maternal emergency', 'Emergency department'),
  JSON_ARRAY('Trauma', 'Maternal emergency', 'Emergency department'),
  0, 0, 10, 4.0,
  'open', '', NULL, 0, '',
  UTC_TIMESTAMP(), UTC_TIMESTAMP()
WHERE NOT EXISTS (
  SELECT 1 FROM hospitals h
  WHERE LOWER(TRIM(h.name)) IN ('madina', 'medina', 'madina hospital', 'medina hospital')
     OR LOWER(TRIM(h.name)) LIKE 'madina hospital%'
     OR LOWER(TRIM(h.name)) LIKE 'medina hospital%'
);

INSERT INTO hospitals (
  name, city, region, district, address,
  latitude, longitude, phone,
  emergency_contacts, services, specialties,
  ambulance_available, ambulance_count, emergency_capacity, rating,
  operating_status, contact_email, owner_user_id, location_verified, logo_url,
  created_at, updated_at
)
SELECT
  'De Martino Public Hospital', 'Mogadishu', 'Banadir', 'Hamar Jajab',
  'Via Londra Road, Hamar Jajab, Mogadishu',
  2.0297769, 45.3386345, '+252615572605',
  JSON_ARRAY('+252615572605'),
  JSON_ARRAY('Emergency department', 'Day care', 'ICU'),
  JSON_ARRAY('Emergency department', 'Day care', 'ICU'),
  0, 0, 10, 4.0,
  'open', '', NULL, 1, '',
  UTC_TIMESTAMP(), UTC_TIMESTAMP()
WHERE NOT EXISTS (
  SELECT 1 FROM hospitals h
  WHERE LOWER(TRIM(h.name)) IN (
    'de martino public hospital', 'de martino hospital', 'demartino hospital'
  )
     OR LOWER(TRIM(h.name)) LIKE 'de martino %'
     OR LOWER(TRIM(h.name)) = 'de martino'
);

INSERT INTO hospitals (
  name, city, region, district, address,
  latitude, longitude, phone,
  emergency_contacts, services, specialties,
  ambulance_available, ambulance_count, emergency_capacity, rating,
  operating_status, contact_email, owner_user_id, location_verified, logo_url,
  created_at, updated_at
)
SELECT
  'Erdogan Hospital (Digfer)', 'Mogadishu', 'Banadir', 'Hodan',
  'Digfer area, Hodan District, Mogadishu',
  2.0437655, 45.3055247, '+252613773333',
  JSON_ARRAY('+252613773333'),
  JSON_ARRAY('Emergency department', 'ICU', 'Surgery', 'Teaching hospital'),
  JSON_ARRAY('Emergency department', 'ICU', 'Surgery', 'Teaching hospital'),
  0, 0, 10, 4.0,
  'open', '', NULL, 1, '',
  UTC_TIMESTAMP(), UTC_TIMESTAMP()
WHERE NOT EXISTS (
  SELECT 1 FROM hospitals h
  WHERE LOWER(TRIM(h.name)) IN (
    'erdogan hospital (digfer)', 'erdogan hospital', 'digfer hospital'
  )
     OR LOWER(TRIM(h.name)) LIKE 'erdogan hospital%'
     OR LOWER(TRIM(h.name)) LIKE 'erdo_an hospital%'
);

INSERT INTO hospitals (
  name, city, region, district, address,
  latitude, longitude, phone,
  emergency_contacts, services, specialties,
  ambulance_available, ambulance_count, emergency_capacity, rating,
  operating_status, contact_email, owner_user_id, location_verified, logo_url,
  created_at, updated_at
)
SELECT
  'Wadajir Hospital', 'Mogadishu', 'Banadir', 'Wadajir',
  'Jaale Siyaad Road, Mogadishu',
  2.0206158, 45.2913877, '+252610598888',
  JSON_ARRAY('+252610598888'),
  JSON_ARRAY('Emergency department', 'ICU', 'Ambulance transport'),
  JSON_ARRAY('Emergency department', 'ICU', 'Ambulance transport'),
  1, 1, 10, 4.0,
  'open', '', NULL, 1, '',
  UTC_TIMESTAMP(), UTC_TIMESTAMP()
WHERE NOT EXISTS (
  SELECT 1 FROM hospitals h
  WHERE LOWER(TRIM(h.name)) IN ('wadajir hospital')
     OR LOWER(TRIM(h.name)) LIKE 'wadajir hospital%'
);

-- ---------------------------------------------------------------------
-- POLICE — response_stations.kind = 'police'
-- ---------------------------------------------------------------------

INSERT INTO response_stations (
  kind, name, city, region, district, address,
  latitude, longitude, phone, operating_status,
  owner_user_id, created_at, updated_at
)
SELECT
  'police', 'Wartanabada District Police Station', 'Mogadishu', 'Banadir',
  'Wartanabada', 'Jidka Isbartiimadda, Mogadishu',
  2.0542523, 45.3349770, '+252617452365', 'open',
  NULL, UTC_TIMESTAMP(), UTC_TIMESTAMP()
WHERE NOT EXISTS (
  SELECT 1 FROM response_stations s
  WHERE s.kind = 'police'
    AND (
      LOWER(TRIM(s.name)) = 'wartanabada district police station'
      OR LOWER(TRIM(s.name)) LIKE 'wartanabada%police%'
    )
);

INSERT INTO response_stations (
  kind, name, city, region, district, address,
  latitude, longitude, phone, operating_status,
  owner_user_id, created_at, updated_at
)
SELECT
  'police', 'C/Casiis (Cabdulaziz) Police Station', 'Mogadishu', 'Banadir',
  '', 'Plus Code 29Q2+JW6, Mogadishu',
  2.0390262, 45.3522746, '+252616576715', 'open',
  NULL, UTC_TIMESTAMP(), UTC_TIMESTAMP()
WHERE NOT EXISTS (
  SELECT 1 FROM response_stations s
  WHERE s.kind = 'police'
    AND (
      LOWER(TRIM(s.name)) = 'c/casiis (cabdulaziz) police station'
      OR LOWER(TRIM(s.name)) LIKE '%cabdulaziz%police%'
      OR LOWER(TRIM(s.name)) LIKE 'c/casiis%police%'
    )
);

INSERT INTO response_stations (
  kind, name, city, region, district, address,
  latitude, longitude, phone, operating_status,
  owner_user_id, created_at, updated_at
)
SELECT
  'police', 'Medina (Wadajir) Police Station', 'Mogadishu', 'Banadir',
  'Wadajir', 'Jaale Siyaad Road, Mogadishu',
  2.0213501, 45.2908808, '', 'open',
  NULL, UTC_TIMESTAMP(), UTC_TIMESTAMP()
WHERE NOT EXISTS (
  SELECT 1 FROM response_stations s
  WHERE s.kind = 'police'
    AND (
      LOWER(TRIM(s.name)) = 'medina (wadajir) police station'
      OR LOWER(TRIM(s.name)) LIKE 'medina%wadajir%police%'
      OR LOWER(TRIM(s.name)) = 'wadajir police station'
    )
);

INSERT INTO response_stations (
  kind, name, city, region, district, address,
  latitude, longitude, phone, operating_status,
  owner_user_id, created_at, updated_at
)
SELECT
  'police', 'Yaaqshid Police Station', 'Mogadishu', 'Banadir',
  'Yaqshid', 'Plus Code 387X+2CV, Mogadishu',
  2.0626250, 45.3485314, '', 'open',
  NULL, UTC_TIMESTAMP(), UTC_TIMESTAMP()
WHERE NOT EXISTS (
  SELECT 1 FROM response_stations s
  WHERE s.kind = 'police'
    AND (
      LOWER(TRIM(s.name)) = 'yaaqshid police station'
      OR LOWER(TRIM(s.name)) LIKE 'yaaqshid%police%'
      OR LOWER(TRIM(s.name)) LIKE 'yaqshid%police%'
    )
);

-- ---------------------------------------------------------------------
-- FIRE — response_stations.kind = 'fire'
-- ---------------------------------------------------------------------

INSERT INTO response_stations (
  kind, name, city, region, district, address,
  latitude, longitude, phone, operating_status,
  owner_user_id, created_at, updated_at
)
SELECT
  'fire', 'Mogadishu Fire Brigade (Fire and Emergency Service)',
  'Mogadishu', 'Banadir', '',
  'Plus Code area 28RW+RGV / 28RW+GH, Mogadishu',
  2.0417, 45.3464, '+252615852642', 'open',
  NULL, UTC_TIMESTAMP(), UTC_TIMESTAMP()
WHERE NOT EXISTS (
  SELECT 1 FROM response_stations s
  WHERE s.kind = 'fire'
    AND (
      LOWER(TRIM(s.name)) = 'mogadishu fire brigade (fire and emergency service)'
      OR LOWER(TRIM(s.name)) LIKE 'mogadishu fire brigade%'
    )
);

-- ---------------------------------------------------------------------
-- OPTIONAL: close known placeholder / demo stations (COMMENTED OUT)
-- Uncomment only if you want maps/dispatch to ignore demo units.
-- Does NOT delete rows (keeps FKs safe).
-- ---------------------------------------------------------------------
-- UPDATE response_stations
-- SET operating_status = 'closed', updated_at = UTC_TIMESTAMP()
-- WHERE LOWER(TRIM(name)) IN (
--   'police response unit',
--   'fire & rescue station',
--   'fire and rescue station'
-- );

-- ---------------------------------------------------------------------
-- Review queries (run before COMMIT)
-- ---------------------------------------------------------------------
SELECT id, name, district, latitude, longitude, phone, location_verified, operating_status
FROM hospitals
ORDER BY id;

SELECT id, kind, name, district, latitude, longitude, phone, operating_status
FROM response_stations
ORDER BY kind, id;

SELECT
  (SELECT COUNT(*) FROM hospitals) AS hospitals_total,
  (SELECT COUNT(*) FROM response_stations WHERE kind = 'police') AS police_stations,
  (SELECT COUNT(*) FROM response_stations WHERE kind = 'fire') AS fire_stations;

-- Flag leftover alias pairs (manual merge if count > 0)
SELECT LOWER(TRIM(name)) AS name_key, COUNT(*) AS cnt, GROUP_CONCAT(id ORDER BY id) AS ids
FROM hospitals
GROUP BY LOWER(TRIM(name))
HAVING COUNT(*) > 1;

-- When satisfied:
--   COMMIT;
-- If not:
--   ROLLBACK;
