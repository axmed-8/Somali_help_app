-- Email verification columns for existing GurmadNet MySQL databases
-- Safe to re-run after checking columns exist (prefer app ensure_email_verification_schema).

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS email_verified TINYINT(1) NOT NULL DEFAULT 0 AFTER reset_expires;

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS email_verify_token VARCHAR(128) NULL AFTER email_verified;

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS email_verify_expires DATETIME NULL AFTER email_verify_token;

-- Keep existing accounts usable
UPDATE users SET email_verified = 1 WHERE email_verified = 0 AND email_verify_token IS NULL;
