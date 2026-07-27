-- Driver GPS share link token (mobile page without hospital login)
ALTER TABLE ambulance_units
  ADD COLUMN IF NOT EXISTS gps_share_token VARCHAR(64) DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_amb_gps_token ON ambulance_units (gps_share_token);
