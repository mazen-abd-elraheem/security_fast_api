-- ====================================================
-- SecureTrack Platform — Database Schema
-- Security Field Force Management System
-- MySQL 8.0+ / InnoDB
-- ====================================================

CREATE DATABASE IF NOT EXISTS securetrack_db
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE securetrack_db;

-- ====================================================
-- 1. Users
-- ====================================================
CREATE TABLE IF NOT EXISTS users (
    user_id       VARCHAR(36) PRIMARY KEY,
    name          VARCHAR(255)  NOT NULL,
    email         VARCHAR(255)  NOT NULL UNIQUE,
    phone_number  VARCHAR(20)   NULL,
    password_hash VARCHAR(255)  NOT NULL,
    role          VARCHAR(30)   NOT NULL DEFAULT 'guard',
    badge_number  VARCHAR(50)   NULL UNIQUE,
    region        VARCHAR(100)  NULL,
    latitude      DOUBLE        NULL,
    longitude     DOUBLE        NULL,
    profile_image_url VARCHAR(500) NULL,
    is_active     BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_users_email (email),
    INDEX idx_users_role (role),
    INDEX idx_users_region (region),
    INDEX idx_users_badge (badge_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ====================================================
-- 2. Sites (Geofenced Locations)
-- ====================================================
CREATE TABLE IF NOT EXISTS sites (
    site_id       VARCHAR(36) PRIMARY KEY,
    name          VARCHAR(255)  NOT NULL,
    address       VARCHAR(500)  NULL,
    latitude      DOUBLE        NOT NULL,
    longitude     DOUBLE        NOT NULL,
    radius_meters INT           NOT NULL DEFAULT 100,
    region        VARCHAR(100)  NULL,
    status        VARCHAR(20)   NOT NULL DEFAULT 'active',
    created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_sites_region (region),
    INDEX idx_sites_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ====================================================
-- 3. Shifts
-- ====================================================
CREATE TABLE IF NOT EXISTS shifts (
    shift_id          VARCHAR(36) PRIMARY KEY,
    site_id           VARCHAR(36)  NOT NULL,
    start_time        TIME         NOT NULL,
    end_time          TIME         NOT NULL,
    days_of_week      VARCHAR(100) NOT NULL DEFAULT 'mon,tue,wed,thu,fri,sat,sun',
    required_headcount INT         NOT NULL DEFAULT 1,
    label             VARCHAR(100) NULL,
    is_active         BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    FOREIGN KEY (site_id) REFERENCES sites(site_id) ON DELETE CASCADE,
    INDEX idx_shifts_site (site_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ====================================================
-- 4. Guard Roster
-- ====================================================
CREATE TABLE IF NOT EXISTS guard_roster (
    roster_id     VARCHAR(36) PRIMARY KEY,
    guard_id      VARCHAR(36)  NOT NULL,
    shift_id      VARCHAR(36)  NOT NULL,
    assigned_date DATE         NOT NULL,
    status        VARCHAR(20)  NOT NULL DEFAULT 'scheduled',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (guard_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (shift_id) REFERENCES shifts(shift_id) ON DELETE CASCADE,
    INDEX idx_roster_guard (guard_id),
    INDEX idx_roster_shift (shift_id),
    INDEX idx_roster_date (assigned_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ====================================================
-- 5. Supervisor Routes
-- ====================================================
CREATE TABLE IF NOT EXISTS supervisor_routes (
    route_id      VARCHAR(36) PRIMARY KEY,
    supervisor_id VARCHAR(36)  NOT NULL,
    site_id       VARCHAR(36)  NOT NULL,
    assigned_date DATE         NOT NULL,
    visit_order   INT          NOT NULL DEFAULT 1,
    status        VARCHAR(20)  NOT NULL DEFAULT 'pending',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (supervisor_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (site_id) REFERENCES sites(site_id) ON DELETE CASCADE,
    INDEX idx_routes_supervisor (supervisor_id),
    INDEX idx_routes_site (site_id),
    INDEX idx_routes_date (assigned_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ====================================================
-- 6. Supervisor Visits (Core Anti-Fraud Records)
-- ====================================================
CREATE TABLE IF NOT EXISTS supervisor_visits (
    visit_id         VARCHAR(36) PRIMARY KEY,
    supervisor_id    VARCHAR(36)  NOT NULL,
    site_id          VARCHAR(36)  NOT NULL,
    route_id         VARCHAR(36)  NULL,
    check_in_time    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    check_in_lat     DOUBLE       NOT NULL,
    check_in_lng     DOUBLE       NOT NULL,
    distance_from_site DOUBLE     NULL,
    check_out_time   DATETIME     NULL,
    check_out_lat    DOUBLE       NULL,
    check_out_lng    DOUBLE       NULL,
    is_verified      BOOLEAN      NOT NULL DEFAULT TRUE,
    photo_url        VARCHAR(500) NULL,
    notes            TEXT         NULL,
    created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (supervisor_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (site_id) REFERENCES sites(site_id) ON DELETE CASCADE,
    FOREIGN KEY (route_id) REFERENCES supervisor_routes(route_id) ON DELETE SET NULL,
    INDEX idx_visits_supervisor (supervisor_id),
    INDEX idx_visits_site (site_id),
    INDEX idx_visits_route (route_id),
    INDEX idx_visits_checkin (check_in_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ====================================================
-- 7. Attendance Logs
-- ====================================================
CREATE TABLE IF NOT EXISTS attendance_logs (
    log_id             VARCHAR(36) PRIMARY KEY,
    roster_id          VARCHAR(36)  NOT NULL,
    visit_id           VARCHAR(36)  NOT NULL,
    supervisor_id      VARCHAR(36)  NOT NULL,
    status             VARCHAR(20)  NOT NULL,
    replacement_guard_id VARCHAR(36) NULL,
    notes              TEXT         NULL,
    recorded_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    checkout_at        DATETIME     NULL,

    FOREIGN KEY (roster_id) REFERENCES guard_roster(roster_id) ON DELETE CASCADE,
    FOREIGN KEY (visit_id) REFERENCES supervisor_visits(visit_id) ON DELETE CASCADE,
    FOREIGN KEY (supervisor_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (replacement_guard_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_attendance_roster (roster_id),
    INDEX idx_attendance_visit (visit_id),
    INDEX idx_attendance_recorded (recorded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ====================================================
-- 8. Incidents
-- ====================================================
CREATE TABLE IF NOT EXISTS incidents (
    incident_id  VARCHAR(36) PRIMARY KEY,
    site_id      VARCHAR(36)  NOT NULL,
    reported_by  VARCHAR(36)  NOT NULL,
    visit_id     VARCHAR(36)  NULL,
    title        VARCHAR(255) NOT NULL,
    description  TEXT         NULL,
    category     VARCHAR(50)  NOT NULL DEFAULT 'other',
    severity     VARCHAR(20)  NOT NULL DEFAULT 'medium',
    status       VARCHAR(20)  NOT NULL DEFAULT 'open',
    photo_url    VARCHAR(500) NULL,
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at  DATETIME     NULL,
    updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    FOREIGN KEY (site_id) REFERENCES sites(site_id) ON DELETE CASCADE,
    FOREIGN KEY (reported_by) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (visit_id) REFERENCES supervisor_visits(visit_id) ON DELETE SET NULL,
    INDEX idx_incidents_site (site_id),
    INDEX idx_incidents_status (status),
    INDEX idx_incidents_severity (severity)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ====================================================
-- 9. Device Registry
-- ====================================================
CREATE TABLE IF NOT EXISTS device_registry (
    registry_id  VARCHAR(36) PRIMARY KEY,
    user_id      VARCHAR(36)  NOT NULL,
    device_id    VARCHAR(255) NOT NULL,
    device_model VARCHAR(255) NULL,
    os_version   VARCHAR(100) NULL,
    is_trusted   BOOLEAN      NOT NULL DEFAULT TRUE,
    registered_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at DATETIME     NULL,

    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    INDEX idx_device_user (user_id),
    INDEX idx_device_device_id (device_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ====================================================
-- 10. Client Sites
-- ====================================================
CREATE TABLE IF NOT EXISTS client_sites (
    client_site_id VARCHAR(36) PRIMARY KEY,
    client_id      VARCHAR(36) NOT NULL,
    site_id        VARCHAR(36) NOT NULL,
    assigned_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (client_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (site_id) REFERENCES sites(site_id) ON DELETE CASCADE,
    INDEX idx_client_sites_client (client_id),
    INDEX idx_client_sites_site (site_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ====================================================
-- 11. Notifications
-- ====================================================
CREATE TABLE IF NOT EXISTS notifications (
    notification_id VARCHAR(36) PRIMARY KEY,
    user_id         VARCHAR(36)  NOT NULL,
    notif_type      VARCHAR(50)  NOT NULL DEFAULT 'system',
    title           VARCHAR(255) NOT NULL,
    message         TEXT         NULL,
    is_read         BOOLEAN      NOT NULL DEFAULT FALSE,
    reference_id    VARCHAR(36)  NULL,
    reference_type  VARCHAR(50)  NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    INDEX idx_notif_user (user_id),
    INDEX idx_notif_read (is_read)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ====================================================
-- 12. Admin Audit Logs
-- ====================================================
CREATE TABLE IF NOT EXISTS admin_audit_logs (
    log_id       VARCHAR(36) PRIMARY KEY,
    admin_id     VARCHAR(36)  NOT NULL,
    admin_name   VARCHAR(255) NOT NULL,
    action       VARCHAR(100) NOT NULL,
    target_type  VARCHAR(50)  NULL,
    target_id    VARCHAR(36)  NULL,
    target_name  VARCHAR(255) NULL,
    description  TEXT         NULL,
    details      JSON         NULL,
    severity     VARCHAR(20)  NOT NULL DEFAULT 'info',
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_audit_admin (admin_id),
    INDEX idx_audit_action (action),
    INDEX idx_audit_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
