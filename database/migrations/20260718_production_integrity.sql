-- GurmadNet AI — Production integrity migration (idempotent-friendly)
-- Apply on existing MySQL databases after backup.
-- Preferred: python -c "from database import mysql_store as m; print(m.ensure_production_integrity())"

USE gurmad;

-- 1) Role ENUM must include super_admin / call_center
ALTER TABLE users MODIFY COLUMN role
  ENUM('citizen','hospital','police','fire','admin','super_admin','call_center')
  NOT NULL DEFAULT 'citizen';

-- 2) Admin notification preference columns (no-op if already present — use Python ensure)
-- ALTER TABLE users ADD COLUMN notify_email_on_sos TINYINT(1) NOT NULL DEFAULT 1;
-- ALTER TABLE users ADD COLUMN notify_email_on_dispatch TINYINT(1) NOT NULL DEFAULT 1;

-- 3) Normalize empty national_id_hash before unique index
UPDATE users
SET national_id_hash = NULL
WHERE national_id_hash IS NOT NULL AND TRIM(national_id_hash) = '';

-- 4) Nullable message sender (required for FK ON DELETE SET NULL)
UPDATE messages m
LEFT JOIN users u ON u.id = m.sender_id
SET m.sender_id = NULL
WHERE m.sender_id = 0 OR (m.sender_id IS NOT NULL AND u.id IS NULL);

ALTER TABLE messages MODIFY COLUMN sender_id INT NULL;

-- 5) Orphan cleanup before new FKs
UPDATE hospitals h
LEFT JOIN users u ON u.id = h.owner_user_id
SET h.owner_user_id = NULL
WHERE h.owner_user_id IS NOT NULL AND u.id IS NULL;

UPDATE call_center_calls c
LEFT JOIN users u ON u.id = c.operator_id
SET c.operator_id = NULL
WHERE c.operator_id IS NOT NULL AND u.id IS NULL;

UPDATE call_center_calls c
LEFT JOIN users u ON u.id = c.user_id
SET c.user_id = NULL
WHERE c.user_id IS NOT NULL AND u.id IS NULL;

UPDATE emergencies e
LEFT JOIN users u ON u.id = e.user_id
SET e.user_id = NULL
WHERE e.user_id IS NOT NULL AND u.id IS NULL;

UPDATE emergencies e
LEFT JOIN hospitals h ON h.id = e.assigned_hospital_id
SET e.assigned_hospital_id = NULL
WHERE e.assigned_hospital_id IS NOT NULL AND h.id IS NULL;

UPDATE audit_logs a
LEFT JOIN users u ON u.id = a.user_id
SET a.user_id = NULL
WHERE a.user_id IS NOT NULL AND u.id IS NULL;

DELETE n FROM notifications n
LEFT JOIN emergencies e ON e.id = n.request_id
WHERE n.request_id IS NOT NULL AND e.id IS NULL;

DELETE m FROM messages m
LEFT JOIN emergencies e ON e.id = m.request_id
WHERE e.id IS NULL;

UPDATE ai_analysis t LEFT JOIN emergencies e ON e.id = t.emergency_id
SET t.emergency_id = NULL WHERE t.emergency_id IS NOT NULL AND e.id IS NULL;
UPDATE ai_recommendation t LEFT JOIN emergencies e ON e.id = t.emergency_id
SET t.emergency_id = NULL WHERE t.emergency_id IS NOT NULL AND e.id IS NULL;
UPDATE ai_dispatch_log t LEFT JOIN emergencies e ON e.id = t.emergency_id
SET t.emergency_id = NULL WHERE t.emergency_id IS NOT NULL AND e.id IS NULL;
UPDATE ai_memory t LEFT JOIN emergencies e ON e.id = t.emergency_id
SET t.emergency_id = NULL WHERE t.emergency_id IS NOT NULL AND e.id IS NULL;

-- 6) Indexes / FKs: applied idempotently by ensure_production_integrity()
-- Prefer running the Python helper rather than raw ALTER (avoids duplicate-name errors).

-- python -c "from database import mysql_store as m; print(m.ensure_production_integrity()); print(m.verify_schema())"
