-- GurmadNet AI — MySQL Schema (production-ready)
-- Run: python scripts/init_mysql.py
-- Then: ensure_production_integrity() applies deferred FKs / orphan cleanup idempotently.

CREATE DATABASE IF NOT EXISTS gurmad CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE gurmad;

CREATE TABLE IF NOT EXISTS hospitals (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(180) NOT NULL,
  city VARCHAR(80) DEFAULT '',
  region VARCHAR(80) DEFAULT '',
  district VARCHAR(80) DEFAULT '',
  address VARCHAR(255) DEFAULT '',
  latitude DOUBLE NOT NULL,
  longitude DOUBLE NOT NULL,
  phone VARCHAR(40) DEFAULT '',
  emergency_contacts JSON,
  services JSON,
  specialties JSON,
  ambulance_available TINYINT(1) DEFAULT 0,
  ambulance_count INT DEFAULT 0,
  emergency_capacity INT DEFAULT 10,
  rating DOUBLE DEFAULT 4.0,
  operating_status ENUM('open','limited','closed') NOT NULL DEFAULT 'open',
  contact_email VARCHAR(180) DEFAULT '',
  owner_user_id INT NULL,
  location_verified TINYINT(1) DEFAULT 0,
  logo_url VARCHAR(255) DEFAULT '',
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  INDEX idx_hospitals_region (region),
  INDEX idx_hospitals_city (city),
  INDEX idx_hospitals_status (operating_status),
  INDEX idx_hospitals_owner (owner_user_id)
);

CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(80) DEFAULT '',
  name VARCHAR(120) NOT NULL,
  email VARCHAR(180) NOT NULL UNIQUE,
  phone VARCHAR(40) DEFAULT '',
  password_hash VARCHAR(255) NOT NULL,
  role ENUM('citizen','hospital','police','fire','admin','super_admin','call_center') NOT NULL DEFAULT 'citizen',
  status ENUM('active','blocked') NOT NULL DEFAULT 'active',
  profile_photo MEDIUMTEXT,
  emergency_contact_name VARCHAR(120) DEFAULT '',
  emergency_contact_phone VARCHAR(40) DEFAULT '',
  emergency_contact_relation VARCHAR(60) DEFAULT '',
  emergency_contact_email VARCHAR(180) DEFAULT '',
  address VARCHAR(255) DEFAULT '',
  city VARCHAR(80) DEFAULT '',
  date_of_birth VARCHAR(20) DEFAULT '',
  gender VARCHAR(20) DEFAULT '',
  first_name VARCHAR(60) DEFAULT '',
  middle_name VARCHAR(60) DEFAULT '',
  last_name VARCHAR(60) DEFAULT '',
  national_id_last4 VARCHAR(4) DEFAULT '',
  national_id_hash VARCHAR(64) NULL,
  national_id_encrypted TEXT NULL,
  blood_type VARCHAR(10) DEFAULT '',
  medical_notes TEXT,
  allergies VARCHAR(500) DEFAULT '',
  saved_locations JSON,
  hospital_id INT NULL,
  station_id INT NULL,
  call_center_id INT NULL,
  reset_token VARCHAR(64) NULL,
  reset_expires DATETIME NULL,
  email_verified TINYINT(1) NOT NULL DEFAULT 0,
  email_verify_token VARCHAR(128) NULL,
  email_verify_expires DATETIME NULL,
  email_verify_attempts INT NOT NULL DEFAULT 0,
  reset_otp_attempts INT NOT NULL DEFAULT 0,
  failed_logins INT NOT NULL DEFAULT 0,
  locked_until DATETIME NULL,
  notify_email_on_sos TINYINT(1) NOT NULL DEFAULT 1,
  notify_email_on_dispatch TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL,
  last_login DATETIME NULL,
  last_seen_call_center DATETIME NULL,
  activity JSON,
  UNIQUE INDEX uq_users_national_id_hash (national_id_hash),
  INDEX idx_users_username (username),
  INDEX idx_users_role (role),
  INDEX idx_users_role_status (role, status),
  INDEX idx_users_hospital (hospital_id),
  INDEX idx_users_station (station_id),
  INDEX idx_users_call_center (call_center_id),
  INDEX idx_users_email_verify_token (email_verify_token),
  INDEX idx_users_reset_token (reset_token),
  CONSTRAINT fk_users_hospital FOREIGN KEY (hospital_id) REFERENCES hospitals(id) ON DELETE SET NULL
);

-- status values used by app: pending, pending_hospital, accepted, dispatched,
-- in_progress, completed, resolved, cancelled, no_hospital_available
CREATE TABLE IF NOT EXISTS emergencies (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NULL,
  type VARCHAR(40) NOT NULL,
  status VARCHAR(40) NOT NULL DEFAULT 'pending',
  location TEXT,
  district VARCHAR(120) DEFAULT '',
  latitude DOUBLE NULL,
  longitude DOUBLE NULL,
  notes TEXT,
  caller_name VARCHAR(120) DEFAULT '',
  phone VARCHAR(40) DEFAULT '',
  assigned_to VARCHAR(40) DEFAULT 'hospital',
  assigned_team_label VARCHAR(120) DEFAULT '',
  assigned_hospital_id INT NULL,
  assigned_hospital_name VARCHAR(180) DEFAULT '',
  assigned_station_id INT NULL,
  hospital_distance_km DOUBLE NULL,
  tracking_active TINYINT(1) DEFAULT 0,
  last_location_update DATETIME NULL,
  accepted_at DATETIME NULL,
  timestamp DATETIME NOT NULL,
  payload JSON,
  INDEX idx_emergencies_user (user_id),
  INDEX idx_emergencies_status (status),
  INDEX idx_emergencies_hospital (assigned_hospital_id),
  INDEX idx_emergencies_station (assigned_station_id),
  INDEX idx_emergencies_timestamp (timestamp),
  INDEX idx_emergencies_status_ts (status, timestamp),
  INDEX idx_emergencies_type_status (type, status),
  CONSTRAINT fk_emergencies_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT fk_emergencies_hospital FOREIGN KEY (assigned_hospital_id) REFERENCES hospitals(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS notifications (
  id INT AUTO_INCREMENT PRIMARY KEY,
  target_type VARCHAR(20) NOT NULL,
  target_id INT NOT NULL,
  message TEXT NOT NULL,
  ntype VARCHAR(40) DEFAULT 'system_alert',
  request_id INT NULL,
  is_read TINYINT(1) DEFAULT 0,
  timestamp DATETIME NOT NULL,
  INDEX idx_notif_target (target_type, target_id),
  INDEX idx_notif_request (request_id),
  INDEX idx_notif_read (is_read),
  INDEX idx_notif_target_read (target_type, target_id, is_read),
  CONSTRAINT fk_notif_request FOREIGN KEY (request_id) REFERENCES emergencies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
  id INT AUTO_INCREMENT PRIMARY KEY,
  request_id INT NOT NULL,
  sender_role VARCHAR(20) NOT NULL,
  sender_id INT NULL,
  text MEDIUMTEXT NOT NULL,
  msg_type VARCHAR(20) DEFAULT 'text',
  status VARCHAR(20) DEFAULT 'sent',
  timestamp DATETIME NOT NULL,
  delivered_at DATETIME NULL,
  seen_at DATETIME NULL,
  INDEX idx_messages_request (request_id),
  INDEX idx_messages_sender (sender_id),
  INDEX idx_messages_request_ts (request_id, timestamp),
  CONSTRAINT fk_messages_request FOREIGN KEY (request_id) REFERENCES emergencies(id) ON DELETE CASCADE,
  CONSTRAINT fk_messages_sender FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS announcements (
  id INT AUTO_INCREMENT PRIMARY KEY,
  title VARCHAR(255) NOT NULL,
  body TEXT NOT NULL,
  priority VARCHAR(20) DEFAULT 'info',
  timestamp DATETIME NOT NULL,
  INDEX idx_announcements_timestamp (timestamp)
);

CREATE TABLE IF NOT EXISTS settings (
  id INT PRIMARY KEY DEFAULT 1,
  payload JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS system_content (
  id INT PRIMARY KEY DEFAULT 1,
  payload JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  action VARCHAR(80) NOT NULL,
  entity_type VARCHAR(40) NOT NULL,
  entity_id INT NULL,
  user_id INT NULL,
  details JSON,
  timestamp DATETIME NOT NULL,
  INDEX idx_audit_action (action),
  INDEX idx_audit_entity (entity_type, entity_id),
  INDEX idx_audit_timestamp (timestamp),
  INDEX idx_audit_user (user_id),
  CONSTRAINT fk_audit_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

-- Call Center Emergency Dispatch (Method 2)
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
  INDEX idx_cc_status_start (status, start_time),
  CONSTRAINT fk_cc_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT fk_cc_operator FOREIGN KEY (operator_id) REFERENCES users(id) ON DELETE SET NULL
);

-- Facility registries (police/fire stations, ambulance fleet, call centers)
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
  INDEX idx_stations_owner (owner_user_id)
);

CREATE TABLE IF NOT EXISTS ambulance_units (
  id INT AUTO_INCREMENT PRIMARY KEY,
  hospital_id INT NOT NULL,
  call_sign VARCHAR(80) NOT NULL,
  plate_number VARCHAR(40) DEFAULT '',
  status ENUM('available','busy','maintenance','offline') NOT NULL DEFAULT 'available',
  latitude DOUBLE NULL,
  longitude DOUBLE NULL,
  driver_name VARCHAR(120) DEFAULT '',
  driver_phone VARCHAR(40) DEFAULT '',
  driver_photo_url VARCHAR(255) DEFAULT '',
  vehicle_photo_url VARCHAR(255) DEFAULT '',
  gps_share_token VARCHAR(64) DEFAULT '',
  notes TEXT,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  INDEX idx_amb_hospital (hospital_id),
  INDEX idx_amb_status (status),
  INDEX idx_amb_gps_token (gps_share_token),
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
  INDEX idx_cc_fac_owner (owner_user_id)
);

-- Deferred FKs (users/stations circular): applied by ensure_production_integrity()
-- fk_users_station, fk_users_call_center, fk_emergencies_station,
-- fk_stations_owner, fk_cc_fac_owner, fk_hospitals_owner


-- AI Emergency Engine
CREATE TABLE IF NOT EXISTS ai_analysis (
  id INT AUTO_INCREMENT PRIMARY KEY,
  emergency_id INT NULL,
  call_id INT NULL,
  category VARCHAR(40) DEFAULT '',
  gurmad_type VARCHAR(40) DEFAULT '',
  priority VARCHAR(20) DEFAULT '',
  risk_level VARCHAR(20) DEFAULT '',
  confidence DOUBLE DEFAULT 0,
  provider VARCHAR(40) DEFAULT 'rule_based',
  source VARCHAR(40) DEFAULT '',
  payload JSON,
  created_at DATETIME NOT NULL,
  INDEX idx_ai_analysis_emergency (emergency_id),
  INDEX idx_ai_analysis_call (call_id),
  INDEX idx_ai_analysis_created (created_at),
  CONSTRAINT fk_ai_analysis_emergency FOREIGN KEY (emergency_id) REFERENCES emergencies(id) ON DELETE SET NULL,
  CONSTRAINT fk_ai_analysis_call FOREIGN KEY (call_id) REFERENCES call_center_calls(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS ai_recommendation (
  id INT AUTO_INCREMENT PRIMARY KEY,
  analysis_id INT NULL,
  emergency_id INT NULL,
  call_id INT NULL,
  status VARCHAR(20) DEFAULT 'pending',
  confidence DOUBLE DEFAULT 0,
  estimated_arrival_minutes INT NULL,
  provider VARCHAR(40) DEFAULT 'rule_based',
  human_decision VARCHAR(40) DEFAULT '',
  operator_id INT NULL,
  payload JSON,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NULL,
  INDEX idx_ai_rec_emergency (emergency_id),
  INDEX idx_ai_rec_call (call_id),
  INDEX idx_ai_rec_analysis (analysis_id),
  INDEX idx_ai_rec_operator (operator_id),
  INDEX idx_ai_rec_status (status),
  INDEX idx_ai_rec_created (created_at),
  CONSTRAINT fk_ai_rec_analysis FOREIGN KEY (analysis_id) REFERENCES ai_analysis(id) ON DELETE SET NULL,
  CONSTRAINT fk_ai_rec_emergency FOREIGN KEY (emergency_id) REFERENCES emergencies(id) ON DELETE SET NULL,
  CONSTRAINT fk_ai_rec_call FOREIGN KEY (call_id) REFERENCES call_center_calls(id) ON DELETE SET NULL,
  CONSTRAINT fk_ai_rec_operator FOREIGN KEY (operator_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS ai_dispatch_log (
  id INT AUTO_INCREMENT PRIMARY KEY,
  emergency_id INT NULL,
  call_id INT NULL,
  recommendation_id INT NULL,
  analysis_id INT NULL,
  human_decision VARCHAR(40) DEFAULT '',
  operator_id INT NULL,
  payload JSON,
  created_at DATETIME NOT NULL,
  INDEX idx_ai_dlog_emergency (emergency_id),
  INDEX idx_ai_dlog_call (call_id),
  INDEX idx_ai_dlog_recommendation (recommendation_id),
  INDEX idx_ai_dlog_analysis (analysis_id),
  INDEX idx_ai_dlog_operator (operator_id),
  INDEX idx_ai_dlog_created (created_at),
  CONSTRAINT fk_ai_dlog_emergency FOREIGN KEY (emergency_id) REFERENCES emergencies(id) ON DELETE SET NULL,
  CONSTRAINT fk_ai_dlog_call FOREIGN KEY (call_id) REFERENCES call_center_calls(id) ON DELETE SET NULL,
  CONSTRAINT fk_ai_dlog_recommendation FOREIGN KEY (recommendation_id) REFERENCES ai_recommendation(id) ON DELETE SET NULL,
  CONSTRAINT fk_ai_dlog_analysis FOREIGN KEY (analysis_id) REFERENCES ai_analysis(id) ON DELETE SET NULL,
  CONSTRAINT fk_ai_dlog_operator FOREIGN KEY (operator_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS ai_memory (
  id INT AUTO_INCREMENT PRIMARY KEY,
  event_type VARCHAR(40) NOT NULL,
  emergency_id INT NULL,
  call_id INT NULL,
  analysis_id INT NULL,
  recommendation_id INT NULL,
  dispatch_log_id INT NULL,
  payload JSON,
  timestamp DATETIME NOT NULL,
  INDEX idx_ai_mem_type (event_type),
  INDEX idx_ai_mem_emergency (emergency_id),
  INDEX idx_ai_mem_call (call_id),
  INDEX idx_ai_mem_analysis (analysis_id),
  INDEX idx_ai_mem_recommendation (recommendation_id),
  INDEX idx_ai_mem_ts (timestamp),
  CONSTRAINT fk_ai_mem_emergency FOREIGN KEY (emergency_id) REFERENCES emergencies(id) ON DELETE SET NULL,
  CONSTRAINT fk_ai_mem_call FOREIGN KEY (call_id) REFERENCES call_center_calls(id) ON DELETE SET NULL,
  CONSTRAINT fk_ai_mem_analysis FOREIGN KEY (analysis_id) REFERENCES ai_analysis(id) ON DELETE SET NULL,
  CONSTRAINT fk_ai_mem_recommendation FOREIGN KEY (recommendation_id) REFERENCES ai_recommendation(id) ON DELETE SET NULL,
  CONSTRAINT fk_ai_mem_dispatch FOREIGN KEY (dispatch_log_id) REFERENCES ai_dispatch_log(id) ON DELETE SET NULL
);
