# SecureTrack — Phase 4: Database Optimization & Production Readiness

## Goal

Optimize the database for **10,000 concurrent users**, add SQL views for each role, remove the `operational_manager`/`regional_manager` role, deduplicate code, and clean dead code for production.

---

## 1. Database Composite Indexes

The current models have single-column indexes on primary/foreign keys, but the **hot queries** in dashboard, workforce, and attendance all use multi-column filters. Without composite indexes, these queries do full table scans at scale.

### Proposed Indexes

| Table | Index Name | Columns | Reason |
|-------|-----------|---------|--------|
| `gps_tracking_pings` | `ix_pings_user_date` | `(user_id, recorded_at)` | Workforce presence query filters by user + date range |
| `gps_tracking_pings` | `ix_pings_roster_fence` | `(roster_id, is_within_geofence, recorded_at)` | Fake attendance detection scans fence status per roster |
| `attendance_logs` | `ix_attendance_roster_visit` | `(roster_id, visit_id)` | Duplicate check on every attendance record |
| `attendance_logs` | `ix_attendance_supervisor_date` | `(supervisor_id, recorded_at)` | Supervisor daily attendance lookup |
| `attendance_logs` | `ix_attendance_status_date` | `(status, recorded_at)` | Dashboard summary counts by status |
| `guard_roster` | `ix_roster_guard_date` | `(guard_id, assigned_date)` | Guard shift lookup (called every GPS ping) |
| `guard_roster` | `ix_roster_shift_date` | `(shift_id, assigned_date, status)` | Workforce log joins shift→roster→logs |
| `supervisor_visits` | `ix_visits_site_checkin` | `(site_id, check_in_time)` | Dashboard "last visit" query per site |
| `supervisor_visits` | `ix_visits_supervisor_date` | `(supervisor_id, check_in_time)` | Supervisor progress lookup |
| `supervisor_routes` | `ix_routes_supervisor_date` | `(supervisor_id, assigned_date)` | Supervisor daily route fetch |
| `incidents` | `ix_incidents_site_status` | `(site_id, status)` | Dashboard active incidents count |
| `incidents` | `ix_incidents_date` | `(created_at, status)` | Recent incidents feed |
| `notifications` | `ix_notif_user_read` | `(user_id, is_read, created_at)` | Unread notifications count + list |
| `guard_photos` | `ix_photos_guard_date` | `(guard_id, created_at)` | Photo gallery per guard |
| `admin_audit_logs` | `ix_audit_date` | `(created_at, action)` | Audit log timeline |
| `users` | `ix_users_role_active` | `(role, is_active)` | Dashboard role counts |
| `shifts` | `ix_shifts_site_active` | `(site_id, is_active)` | Site shift queries |
| `device_registry` | `ix_devices_user_trusted` | `(user_id, is_trusted)` | Device verification lookup |

### Implementation
Create a new migration file [app/migrations/add_indexes.py](file:///c:/Users/L0Q/Desktop/security%20fast%20api%20project/app/migrations/add_indexes.py) that adds all indexes via raw SQL, then add `__table_args__` with `Index(...)` to each model for ORM sync.

---

## 2. SQL Views for Role-Based Dashboards

Instead of N+1 queries in service layer code, create **materialized-style SQL views** that pre-join the commonly needed data.

### Proposed Views

#### `v_admin_dashboard_summary`
Pre-aggregates: total users by role, active sites, open incidents, today's attendance rate.
```sql
CREATE OR REPLACE VIEW v_admin_dashboard_summary AS
SELECT
    (SELECT COUNT(*) FROM users WHERE is_active = 1 AND role = 'guard') AS total_guards,
    (SELECT COUNT(*) FROM users WHERE is_active = 1 AND role = 'supervisor') AS total_supervisors,
    (SELECT COUNT(*) FROM sites WHERE status = 'active') AS active_sites,
    (SELECT COUNT(*) FROM incidents WHERE status IN ('open', 'investigating')) AS open_incidents;
```

#### `v_site_coverage_today`
Per-site coverage: required guards, present guards, coverage %, last visit time.
```sql
CREATE OR REPLACE VIEW v_site_coverage_today AS
SELECT 
    s.site_id, s.name AS site_name, s.region,
    COALESCE(SUM(sh.required_headcount), 0) AS required_guards,
    -- present count from today's attendance
    ...
FROM sites s
LEFT JOIN shifts sh ON sh.site_id = s.site_id AND sh.is_active = 1
WHERE s.status = 'active'
GROUP BY s.site_id;
```

#### `v_guard_shift_today`
Guard's current assignment: site name, shift times, geo-fence coords, roster status.

#### `v_workforce_daily`
Pre-joined: user + roster + shift + site + attendance for a given date — eliminates the 5-table join in [workforce.py](file:///c:/Users/L0Q/Desktop/security%20fast%20api%20project/app/api/v1/workforce.py).

#### `v_supervisor_progress_today`
Route progress: total assigned, completed, pending, skipped.

#### `v_attendance_summary`
Daily attendance aggregates: present/absent/late/replacement counts per site.

### Implementation
Create [app/migrations/create_views.py](file:///c:/Users/L0Q/Desktop/security%20fast%20api%20project/app/migrations/create_views.py) with raw SQL `CREATE VIEW` statements, executed during app startup after `create_all`.

---

## 3. Remove `regional_manager` Role

> [!IMPORTANT]
> There is **no `operational_manager`** role in the codebase. The closest unused role is **`regional_manager`** — it exists in the enum and in the admin users UI filter list, but has **no dedicated routes, no shell, and no dashboard**. Should I remove `regional_manager`?

### Files to modify:
| File | Change |
|------|--------|
| [enums.py](file:///c:/Users/L0Q/Desktop/security%20fast%20api%20project/app/enums.py) | Remove `REGIONAL_MANAGER` from `UserRole` |
| [admin_users_screen.dart](file:///c:/Users/L0Q/Desktop/security%20fast%20api%20project/securetrack_app/lib/screens/admin/admin_users_screen.dart) | Remove from filter tabs + role dropdowns (lines 22, 27, 485, 787, 900) |
| [sites.py](file:///c:/Users/L0Q/Desktop/security%20fast%20api%20project/app/api/v1/sites.py) | Remove from `require_role()` calls (lines 40, 52) |
| [user.py model](file:///c:/Users/L0Q/Desktop/security%20fast%20api%20project/app/models/user.py) | Remove docstring reference |

---

## 4. Dead Code Removal

### Backend
| File | Issue |
|------|-------|
| [client_site.py](file:///c:/Users/L0Q/Desktop/security%20fast%20api%20project/app/models/client_site.py) | Model exists but **no API or service uses it** — dead code |
| `_haversine()` function | **Duplicated 3 times** in [tracking.py](file:///c:/Users/L0Q/Desktop/security%20fast%20api%20project/app/api/v1/tracking.py), [outdoor.py](file:///c:/Users/L0Q/Desktop/security%20fast%20api%20project/app/api/v1/outdoor.py), [attendance.py](file:///c:/Users/L0Q/Desktop/security%20fast%20api%20project/app/api/v1/attendance.py) — consolidate into `GeoService.haversine_distance_meters()` which already exists |

### Flutter
| File | Issue |
|------|-------|
| [guard_checkin_screen.dart](file:///c:/Users/L0Q/Desktop/security%20fast%20api%20project/securetrack_app/lib/screens/guard/guard_checkin_screen.dart) | **Not imported or routed anywhere** — entirely dead code (no route, no import) |
| `st_button.dart` import in checkin screen | If screen is deleted, its imports are moot |

---

## 5. Database Engine Hardening (10K Users)

Current [database.py](file:///c:/Users/L0Q/Desktop/security%20fast%20api%20project/app/core/database.py) has **zero connection pooling configuration**. At 10K concurrent users, the default SQLAlchemy pool (5 connections, 10 overflow) will instantly saturate.

### Changes to `database.py`:
```python
engine = create_engine(
    settings.DATABASE_URL,
    pool_size=20,           # Base pool connections
    max_overflow=40,        # Extra connections under load
    pool_pre_ping=True,     # Verify connections before use
    pool_recycle=1800,      # Recycle connections every 30 min
    pool_timeout=10,        # Wait max 10s for a connection
    echo=settings.DEBUG,
)
```

### Additional production config:
- Add `WORKERS` setting for uvicorn (`workers = cpu_count * 2 + 1`)
- Add `DATABASE_POOL_SIZE` and `DATABASE_MAX_OVERFLOW` to `config.py`
- Add `pool_pre_ping=True` to handle MySQL "gone away" errors

---

## 6. GPS Tracking & Check-in/Check-out Flow

### Current Flow (already working):
1. Guard opens app → permissions gate in `guard_shell.dart` → grants permissions
2. `BackgroundTrackingService` starts → sends GPS ping every 60s to `/tracking/ping`
3. Server receives ping → finds today's roster → checks geofence → stores `GpsTrackingPing`
4. Workforce API computes presence hours from continuous geofence pings

### What needs fixing:
- The **guard auto-checkin** provider (`guard_checkin_provider.dart`) creates check-in attendance records client-side, but the **check-out is never triggered automatically**
- The background tracking service should trigger an auto-checkout when the guard leaves the geofence for > 5 minutes

### Proposed Improvement:
Add a server-side `/tracking/ping` enhancement: when a guard who was previously `is_within_geofence=True` sends a ping that is **outside** the geofence, and the last N pings (5 min window) are all outside, auto-create a `checkout_at` timestamp on their `AttendanceLog`. This makes check-in/check-out fully GPS-driven with no manual intervention.

---

## 7. Workforce Screen Real-Time Data

The admin workforce screen currently fetches a snapshot. For real-time:
- Add a `?live=true` query parameter to the workforce API that includes **currently active sessions** (guards with check-in but no check-out yet)
- Show a "LIVE" badge on the workforce screen for ongoing shifts
- Use the existing `actual_hours` field that already accounts for `now_utc` when no checkout exists

This is already partially implemented in [workforce.py lines 78-83](file:///c:/Users/L0Q/Desktop/security%20fast%20api%20project/app/api/v1/workforce.py#L78-L83) — it uses current time when no checkout exists. Just need to add a live indicator in the Flutter UI.

---

## Open Questions

> [!IMPORTANT]
> **`regional_manager` removal**: Confirm you want this role removed entirely. Any existing users with `role='regional_manager'` in the database would need to be migrated to `admin` or `supervisor`.

> [!WARNING]
> **Migration strategy**: Should I create an Alembic migration for the indexes/views, or apply them directly in the `lifespan` startup (current pattern)? Alembic is safer for production but adds setup overhead.

---

## Verification Plan

### Automated
```bash
# Backend tests
cd "security fast api project" && python -m pytest tests/ -v

# Flutter analyze
cd securetrack_app && flutter analyze
```

### Manual
- Run `EXPLAIN` on hot queries before/after indexes
- Verify dashboard loads < 200ms with indexes
- Verify GPS ping endpoint handles 100 req/s
- Test auto-checkout triggers correctly after 5 min outside geofence
