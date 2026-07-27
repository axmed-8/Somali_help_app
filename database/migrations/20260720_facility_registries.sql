-- Facility registries + command workflow FKs (idempotent via ensure_production_integrity)
USE gurmad;

CREATE TABLE IF NOT EXISTS response_stations (
  id INT AUTO_INCREMENT PRIMARY KEY,
  kind ENUM('police','fire') NOT NULL,
  name VARCHAR(180) NOT NULL,
  city VARCHAR(80) DEFAULT '',
  region VARCHAR(80) DEFAULT '',
  district VARCHAR(80) DEFAULT '',
  address VARCHAR(255) DEFAULT '',
  latitude DOUBLE NULL,
  longitude DOUBLE NULL,
  phone VARCHAR(40) DEFAULT '',
  operating_status ENUM('open','limited','closed') NOT NULL DEFAULT 'open',
  owner_user_id INT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  INDEX idx_stations_kind (kind),
  INDEX idx_stations_status (operating_status),
  INDEX idx_stations_city (city),
  INDEX idx_stations_owner (owner_user_id),
  CONSTRAINT fk_stations_owner FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS ambulance_units (
  id INT AUTO_INCREMENT PRIMARY KEY,
  hospital_id INT NOT NULL,
  call_sign VARCHAR(80) NOT NULL,
  plate_number VARCHAR(40) DEFAULT '',
  status ENUM('available','busy','maintenance','offline') NOT NULL DEFAULT 'available',
  latitude DOUBLE NULL,
  longitude DOUBLE NULL,
  notes TEXT,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  INDEX idx_amb_hospital (hospital_id),
  INDEX idx_amb_status (status),
  CONSTRAINT fk_amb_hospital FOREIGN KEY (hospital_id) REFERENCES hospitals(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS call_centers (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(180) NOT NULL,
  city VARCHAR(80) DEFAULT '',
  region VARCHAR(80) DEFAULT '',
  district VARCHAR(80) DEFAULT '',
  address VARCHAR(255) DEFAULT '',
  latitude DOUBLE NULL,
  longitude DOUBLE NULL,
  phone VARCHAR(40) DEFAULT '',
  operating_status ENUM('open','limited','closed') NOT NULL DEFAULT 'open',
  owner_user_id INT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  INDEX idx_cc_fac_status (operating_status),
  INDEX idx_cc_fac_city (city),
  INDEX idx_cc_fac_owner (owner_user_id),
  CONSTRAINT fk_cc_fac_owner FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS station_id INT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS call_center_id INT NULL;
ALTER TABLE emergencies ADD COLUMN IF NOT EXISTS assigned_station_id INT NULL;
