-- Hospital-owned ambulances: GurmadNet stores dispatch essentials only
-- (availability, GPS, driver contact). Fleet ops stay with hospitals.

ALTER TABLE ambulance_units
  ADD COLUMN IF NOT EXISTS driver_name VARCHAR(120) DEFAULT '',
  ADD COLUMN IF NOT EXISTS driver_phone VARCHAR(40) DEFAULT '';
