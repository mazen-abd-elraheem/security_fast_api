"""
SecureTrack Platform â€” Database Views
Pre-aggregated SQL views for fast role-based dashboard queries.
Created/replaced on every startup so they always reflect current schema.
"""
import logging
from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# View definitions (SQLite-compatible)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

VIEWS = {
    # â”€â”€ Admin: platform-wide counts â”€â”€
    "v_admin_dashboard_summary": """
        CREATE VIEW IF NOT EXISTS v_admin_dashboard_summary AS
        SELECT
            (SELECT COUNT(*) FROM users WHERE is_active = 1 AND role = 'guard')      AS total_guards,
            (SELECT COUNT(*) FROM users WHERE is_active = 1 AND role = 'outdoor')    AS total_outdoor,
            (SELECT COUNT(*) FROM users WHERE is_active = 1 AND role = 'supervisor') AS total_supervisors,
            (SELECT COUNT(*) FROM users WHERE is_active = 1 AND role = 'admin')      AS total_admins,
            (SELECT COUNT(*) FROM sites WHERE status = 'active')                     AS active_sites,
            (SELECT COUNT(*) FROM incidents WHERE status IN ('open', 'investigating')) AS open_incidents,
            (SELECT COUNT(*) FROM shifts WHERE is_active = 1)                        AS active_shifts
    """,

    # â”€â”€ Per-site coverage: required vs present guards â”€â”€
    "v_site_coverage": """
        CREATE VIEW IF NOT EXISTS v_site_coverage AS
        SELECT
            s.site_id,
            s.name       AS site_name,
            s.region,
            s.latitude,
            s.longitude,
            s.radius_meters,
            COALESCE(sh_agg.required, 0) AS required_guards,
            COALESCE(att_agg.present, 0) AS present_guards
        FROM sites s
        LEFT JOIN (
            SELECT site_id, SUM(required_headcount) AS required
            FROM shifts
            WHERE is_active = 1
            GROUP BY site_id
        ) sh_agg ON sh_agg.site_id = s.site_id
        LEFT JOIN (
            SELECT sv.site_id, COUNT(DISTINCT al.log_id) AS present
            FROM attendance_logs al
            JOIN supervisor_visits sv ON al.visit_id = sv.visit_id
            WHERE al.status IN ('present', 'late', 'replacement')
              AND al.recorded_at >= CURDATE()
            GROUP BY sv.site_id
        ) att_agg ON att_agg.site_id = s.site_id
        WHERE s.status = 'active'
    """,

    # â”€â”€ Guard's current shift for today â”€â”€
    "v_guard_shift_today": """
        CREATE VIEW IF NOT EXISTS v_guard_shift_today AS
        SELECT
            gr.guard_id,
            gr.roster_id,
            gr.assigned_date,
            gr.status AS roster_status,
            sh.shift_id,
            sh.start_time,
            sh.end_time,
            sh.label AS shift_label,
            s.site_id,
            s.name AS site_name,
            s.latitude AS site_lat,
            s.longitude AS site_lng,
            s.radius_meters
        FROM guard_roster gr
        JOIN shifts sh ON sh.shift_id = gr.shift_id
        JOIN sites s ON s.site_id = sh.site_id
        WHERE gr.assigned_date = date('now')
    """,

    # â”€â”€ Supervisor route progress â”€â”€
    "v_supervisor_progress": """
        CREATE VIEW IF NOT EXISTS v_supervisor_progress AS
        SELECT
            sr.supervisor_id,
            u.name AS supervisor_name,
            sr.assigned_date,
            COUNT(*)                                         AS total_assigned,
            SUM(CASE WHEN sr.status = 'completed' THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN sr.status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress,
            SUM(CASE WHEN sr.status = 'pending' THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN sr.status = 'skipped' THEN 1 ELSE 0 END) AS skipped
        FROM supervisor_routes sr
        JOIN users u ON u.user_id = sr.supervisor_id
        GROUP BY sr.supervisor_id, sr.assigned_date
    """,

    # â”€â”€ Attendance summary per site per day â”€â”€
    "v_attendance_summary": """
        CREATE VIEW IF NOT EXISTS v_attendance_summary AS
        SELECT
            sv.site_id,
            date(al.recorded_at) AS log_date,
            COUNT(*)                                                         AS total,
            SUM(CASE WHEN al.status = 'present' THEN 1 ELSE 0 END)          AS present,
            SUM(CASE WHEN al.status = 'absent' THEN 1 ELSE 0 END)           AS absent,
            SUM(CASE WHEN al.status = 'late' THEN 1 ELSE 0 END)             AS late,
            SUM(CASE WHEN al.status = 'replacement' THEN 1 ELSE 0 END)      AS replacement
        FROM attendance_logs al
        LEFT JOIN supervisor_visits sv ON al.visit_id = sv.visit_id
        GROUP BY sv.site_id, date(al.recorded_at)
    """,
}


def create_views(db: Session) -> None:
    """Create or replace all SQL views. Safe to call on every startup."""
    for name, ddl in VIEWS.items():
        try:
            # Drop first to ensure latest schema
            db.execute(text(f"DROP VIEW IF EXISTS {name}"))
            db.execute(text(ddl))
            log.info(f"  âœ“ View {name} created")
        except Exception as e:
            log.warning(f"  âš  Could not create view {name}: {e}")
    db.commit()
