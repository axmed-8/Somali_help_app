-- =============================================================================
-- GurmadNet AI — MySQL Workbench setup (run as root)
-- =============================================================================
-- 1. Open MySQL Workbench
-- 2. Connect as root (or another admin account)
-- 3. File → Open SQL Script → select this file
-- 4. Execute the full script (lightning bolt icon)
-- 5. Then run: python scripts/init_mysql.py
-- 6. Then run: python scripts/migrate_json_to_mysql.py
-- =============================================================================

-- Create application database
CREATE DATABASE IF NOT EXISTS `gurmad`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- Remove old app users if they exist (safe to re-run)
DROP USER IF EXISTS 'gurmad_user'@'localhost';
DROP USER IF EXISTS 'sms_user'@'localhost';

-- Application database user
CREATE USER 'sms_user'@'localhost'
  IDENTIFIED BY 'gurmad@1234';

-- Full privileges on the gurmad database only
GRANT ALL PRIVILEGES ON `gurmad`.* TO 'sms_user'@'localhost';

FLUSH PRIVILEGES;

-- Verify (optional — check Output panel)
SELECT 'Database gurmad and user sms_user@localhost are ready.' AS status;
