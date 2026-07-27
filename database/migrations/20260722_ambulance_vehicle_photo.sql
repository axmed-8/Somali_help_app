-- Vehicle (car) photo separate from driver profile photo
ALTER TABLE ambulance_units
  ADD COLUMN IF NOT EXISTS vehicle_photo_url VARCHAR(255) DEFAULT '';
