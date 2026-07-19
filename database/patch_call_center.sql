-- Patch existing GurmadNet MySQL for Call Center role + calls table
-- Preferred: python ensure_call_center_schema + ensure_production_integrity
-- Manual: mysql -u root -p gurmad < database/patch_call_center.sql

USE gurmad;

ALTER TABLE users
  MODIFY COLUMN role ENUM('citizen','hospital','police','fire','admin','super_admin','call_center')
  NOT NULL DEFAULT 'citizen';

CREATE TABLE IF NOT EXISTS call_center_calls (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NULL,
  caller_name VARCHAR(120) NOT NULL,
  phone VARCHAR(40) DEFAULT '',
  latitude DOUBLE NULL,
  longitude DOUBLE NULL,
  address TEXT,
  district VARCHAR(120) DEFAULT '',
  status VARCHAR(40) NOT NULL DEFAULT 'ringing',
  operator_id INT NULL,
  operator_name VARCHAR(120) DEFAULT '',
  emergency_type VARCHAR(40) DEFAULT '',
  emergency_types JSON,
  dispatched_to JSON,
  emergency_ids JSON,
  nearest JSON,
  device_info JSON,
  notes TEXT,
  accuracy_m DOUBLE NULL,
  start_time DATETIME NOT NULL,
  answered_at DATETIME NULL,
  dispatched_at DATETIME NULL,
  end_time DATETIME NULL,
  duration_sec INT DEFAULT 0,
  final_status VARCHAR(40) DEFAULT '',
  source VARCHAR(40) DEFAULT 'call_center',
  INDEX idx_cc_status (status),
  INDEX idx_cc_operator (operator_id),
  INDEX idx_cc_user (user_id),
  INDEX idx_cc_start (start_time),
  INDEX idx_cc_status_start (status, start_time)
);

-- FKs / orphan cleanup: run ensure_production_integrity() after this patch
