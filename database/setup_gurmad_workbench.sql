-- =============================================================================
-- GurmadNet AI — MySQL Workbench setup (run as root)
-- =============================================================================
-- 1. Open MySQL Workbench
-- 2. Connect as root (or another admin account)
-- 3. File → Open SQL Script → select this file
-- 4. Replace CHANGE_ME_STRONG_PASSWORD below with a strong secret
-- 5. Execute the full script (lightning bolt icon)
-- 6. Put the same credentials in database/db_config.env (never commit secrets)
-- 7. Then run: python scripts/init_mysql.py
-- 8. Then run: python scripts/migrate_json_to_mysql.py
-- =============================================================================

-- Create application database
CREATE DATABASE IF NOT EXISTS `gurmad`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- Remove old app users if they exist (safe to re-run)
DROP USER IF EXISTS 'gurmad_user'@'localhost';
DROP USER IF EXISTS 'sms_user'@'localhost';

-- Application database user — REPLACE the password before running
CREATE USER 'sms_user'@'localhost'
  IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';

-- Least privilege for the app: DML + DDL needed for runtime ensure_* migrations
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES
  ON `gurmad`.* TO 'sms_user'@'localhost';

FLUSH PRIVILEGES;

SELECT 'Database gurmad and user sms_user@localhost are ready. Update db_config.env next.' AS status;
