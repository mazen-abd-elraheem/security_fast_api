"""
SecureTrack Platform — Workforce Log Routes
Aggregated guard/outdoor shift tracking: GPS auto-checkin sessions, hours, alerts, salary deductions.
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload
from datetime import date, datetime, timedelta, time as dtime, timezone
from typing import Optional
import csv
import io

from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User
from app.models.guard_roster import GuardRoster
from app.models.shift import Shift
from app.models.site import Site
from app.models.attendance_log import AttendanceLog
from app.models.gps_tracking_ping import GpsTrackingPing
from app.enums import UserRole
from app.api.v1.tracking import compute_presence_hours_from_pings

router = APIRouter()

# ── Deduction Rules (configurable) ──
LATE_THRESHOLD_MINUTES = 10        # Grace period before "late"
ABSENT_DEDUCTION = 200.0           # Full-day deduction for absent
LATE_DEDUCTION_PER_MINUTE = 2.0    # Per-minute deduction for late
EARLY_LEAVE_DEDUCTION = 100.0      # Fixed deduction for leaving early
NO_CHECKOUT_DEDUCTION = 50.0       # No checkout recorded
OUTSIDE_GEOFENCE_DEDUCTION_PER_HOUR = 20.0  # Deduction per hour outside geofence during shift


def _time_to_minutes(t: dtime) -> float:
    """Convert a time object to minutes since midnight."""
    return t.hour * 60 + t.minute + t.second / 60


def _compute_sessions(logs: list[AttendanceLog]) -> list[dict]:
    """
    Build check-in/check-out session pairs from attendance logs.
    Each log has a recorded_at (check-in) and optional checkout_at.
    """
    sessions = []
    for log in sorted(logs, key=lambda l: l.recorded_at):
        checkin_dt = log.recorded_at
        if checkin_dt and checkin_dt.tzinfo is None:
            checkin_dt = checkin_dt.replace(tzinfo=timezone.utc)
            
        checkout_dt = log.checkout_at
        if checkout_dt and checkout_dt.tzinfo is None:
            checkout_dt = checkout_dt.replace(tzinfo=timezone.utc)

        sessions.append({
            "checkin": checkin_dt.isoformat() if checkin_dt else None,
            "checkout": checkout_dt.isoformat() if checkout_dt else None,
            "status": log.status,
            "notes": log.notes or "",
        })
    return sessions


def _compute_actual_hours(sessions: list[dict], shift_end_dt: datetime | None = None) -> float:
    """
    Sum up hours from all sessions.
    If a session has checkin but no checkout:
      - If the shift has ended, use the shift end time as fallback checkout.
      - If the shift is still ongoing, use current time.
    This prevents guards who forget to checkout from showing 0 hours.
    """
    now_utc = datetime.now(timezone.utc)
    total = 0.0
    for s in sessions:
        if s["checkin"]:
            cin = datetime.fromisoformat(s["checkin"])
            if s["checkout"]:
                cout = datetime.fromisoformat(s["checkout"])
            elif shift_end_dt and now_utc >= shift_end_dt:
                # Shift ended, guard didn't checkout — use shift end as fallback
                cout = shift_end_dt
            else:
                # Shift still ongoing — use current time
                cout = now_utc
            diff = (cout - cin).total_seconds() / 3600.0
            if diff > 0:
                total += diff
    return round(total, 2)


def _compute_outside_hours(sessions: list[dict], shift_start_dt: datetime, shift_end_dt: datetime) -> float:
    """
    Calculate the total time a guard spent OUTSIDE the geofence during shift hours.
    This is the gap time between consecutive sessions, clamped to the shift window.
    Only counts gaps that fall within the scheduled shift period.
    """
    if len(sessions) < 2:
        return 0.0

    now_utc = datetime.now(timezone.utc)
    total_outside = 0.0

    for i in range(len(sessions) - 1):
        prev_session = sessions[i]
        next_session = sessions[i + 1]

        # Gap = from previous checkout to next checkin
        if prev_session["checkout"] and next_session["checkin"]:
            gap_start = datetime.fromisoformat(prev_session["checkout"])
            gap_end = datetime.fromisoformat(next_session["checkin"])

            # Clamp to shift boundaries — only count time outside during the shift
            gap_start = max(gap_start, shift_start_dt)
            gap_end = min(gap_end, shift_end_dt)

            gap_hours = (gap_end - gap_start).total_seconds() / 3600.0
            if gap_hours > 0:
                total_outside += gap_hours

    return round(total_outside, 2)


def _build_employee_record(
    user: User,
    roster: GuardRoster,
    shift: Shift,
    site: Site,
    logs: list[AttendanceLog],
    target_date: date,
    gps_pings: list | None = None,
) -> dict:
    """Build a single employee's workforce record."""
    sessions = _compute_sessions(logs)

    # Compute shift start/end datetimes
    scheduled_start = shift.start_time
    scheduled_end = shift.end_time
    shift_start_dt = datetime.combine(target_date, scheduled_start, tzinfo=timezone.utc)
    scheduled_end_dt = datetime.combine(target_date, scheduled_end, tzinfo=timezone.utc)
    if scheduled_end < scheduled_start:
        scheduled_end_dt += timedelta(days=1)

    # GPS-validated presence hours (always compute if pings available)
    gps_presence_hours = compute_presence_hours_from_pings(gps_pings) if gps_pings else 0.0

    # Prefer GPS-validated hours when pings are available
    if gps_pings and gps_presence_hours > 0:
        actual_hours = gps_presence_hours
    else:
        actual_hours = _compute_actual_hours(sessions, shift_end_dt=scheduled_end_dt)

    # Time spent outside geofence during shift hours
    # Prefer server-tracked total_outside_seconds from the attendance log (accurate),
    # fall back to gap-based calculation between sessions for backward compatibility.
    tracked_outside_seconds = sum(
        (log.total_outside_seconds or 0) for log in logs
    )
    if tracked_outside_seconds > 0:
        outside_hours = round(tracked_outside_seconds / 3600.0, 2)
    else:
        outside_hours = _compute_outside_hours(sessions, shift_start_dt, scheduled_end_dt)

    # Scheduled hours
    start_mins = _time_to_minutes(scheduled_start)
    end_mins = _time_to_minutes(scheduled_end)
    if end_mins <= start_mins:
        end_mins += 24 * 60  # overnight shift
    scheduled_hours = round((end_mins - start_mins) / 60, 2)

    # Determine status and alerts
    alerts = []
    salary_deduction = 0.0
    deduction_reasons = []

    if not logs:
        # No check-in at all → ABSENT
        status = "absent"
        alerts.append("No check-in recorded")
        salary_deduction += ABSENT_DEDUCTION
        deduction_reasons.append("Absent")
    else:
        first_checkin = min(l.recorded_at for l in logs)
        if first_checkin.tzinfo is None:
            first_checkin = first_checkin.replace(tzinfo=timezone.utc)

        # Check if late
        scheduled_dt = datetime.combine(target_date, scheduled_start, tzinfo=timezone.utc)
        late_minutes = (first_checkin - scheduled_dt).total_seconds() / 60

        if late_minutes > LATE_THRESHOLD_MINUTES:
            status = "late"
            late_mins_int = int(late_minutes)
            alerts.append(f"Late by {late_mins_int} min")
            salary_deduction += late_minutes * LATE_DEDUCTION_PER_MINUTE
            deduction_reasons.append(f"Late arrival ({late_mins_int}m)")
        else:
            status = "present"

        # Check for re-entries
        if len(sessions) > 1:
            for i, s in enumerate(sessions[1:], start=2):
                alerts.append(f"Re-entry (session {i}) at {s['checkin'][:16] if s['checkin'] else '?'}")

        # Check for missing checkout and early leave (only if shift has ended)
        now_utc = datetime.now(timezone.utc)
        scheduled_end_dt = datetime.combine(target_date, scheduled_end, tzinfo=timezone.utc)
        if scheduled_end < scheduled_start:
            scheduled_end_dt += timedelta(days=1)

        # Only apply checkout-related penalties if the shift is officially over
        if now_utc >= scheduled_end_dt:
            has_checkout = any(s["checkout"] for s in sessions)
            if not has_checkout:
                alerts.append("No check-out recorded")
                salary_deduction += NO_CHECKOUT_DEDUCTION
                deduction_reasons.append("No checkout")
            else:
                # Check for early leave
                last_checkout_str = max(
                    (s["checkout"] for s in sessions if s["checkout"]),
                    default=None
                )
                if last_checkout_str:
                    last_checkout = datetime.fromisoformat(last_checkout_str)
                    early_mins = (scheduled_end_dt - last_checkout).total_seconds() / 60
                    if early_mins > LATE_THRESHOLD_MINUTES:
                        alerts.append(f"Left early by {int(early_mins)} min")
                        salary_deduction += EARLY_LEAVE_DEDUCTION
                        deduction_reasons.append("Early departure")

    # Outside geofence deduction
    if outside_hours > 0:
        outside_deduction = round(outside_hours * OUTSIDE_GEOFENCE_DEDUCTION_PER_HOUR, 2)
        salary_deduction += outside_deduction
        alerts.append(f"Outside geofence for {outside_hours}h")
        deduction_reasons.append(f"Outside geofence ({outside_hours}h)")

    salary_deduction = round(salary_deduction, 2)

    # Is this a live/ongoing session?
    now_utc = datetime.now(timezone.utc)
    shift_end_dt_final = datetime.combine(target_date, scheduled_end, tzinfo=timezone.utc)
    if scheduled_end < scheduled_start:
        shift_end_dt_final += timedelta(days=1)
    has_open_session = sessions and not sessions[-1].get("checkout")
    is_live = bool(has_open_session and now_utc < shift_end_dt_final)

    return {
        "date": target_date.isoformat() if target_date else None,
        "user_id": user.user_id,
        "name": user.name,
        "role": user.role.value if hasattr(user.role, "value") else user.role,
        "site_name": site.name if site else "Unknown",
        "shift_label": shift.label or f"{shift.start_time}-{shift.end_time}",
        "scheduled_start": str(scheduled_start)[:5],
        "scheduled_end": str(scheduled_end)[:5],
        "scheduled_hours": scheduled_hours,
        "first_checkin": sessions[0]["checkin"] if sessions else None,
        "last_checkout": sessions[-1]["checkout"] if sessions and sessions[-1]["checkout"] else None,
        "sessions": sessions,
        "sessions_count": len(sessions),
        "actual_hours": actual_hours,
        "outside_hours": outside_hours,
        "gps_presence_hours": gps_presence_hours,
        "is_live": is_live,
        "status": status,
        "alerts": alerts,
        "salary_deduction": salary_deduction,
        "deduction_reason": ", ".join(deduction_reasons) if deduction_reasons else "None",
    }


@router.get("/log", summary="Workforce log for a date range")
def get_workforce_log(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    role_filter: Optional[str] = Query(None, description="Filter by role: guard, outdoor, or all"),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Aggregated workforce log based on guard GPS auto-checkin data.
    Shows scheduled vs actual hours, sessions (re-entries), alerts, and deductions.
    """
    query = (
        db.query(GuardRoster)
        .options(
            joinedload(GuardRoster.guard),
            joinedload(GuardRoster.shift).joinedload(Shift.site),
            joinedload(GuardRoster.attendance_logs),
        )
        .filter(GuardRoster.assigned_date >= date_from)
        .filter(GuardRoster.assigned_date <= date_to)
        .filter(GuardRoster.status != "canceled")
    )

    rosters = query.all()

    employees = []
    for roster in rosters:
        guard = roster.guard
        shift = roster.shift
        site = shift.site if shift else None

        if not guard or not shift:
            continue

        guard_role = guard.role.value if hasattr(guard.role, "value") else guard.role
        if role_filter and role_filter != "all":
            if guard_role != role_filter:
                continue

        logs = roster.attendance_logs or []

        # Fetch GPS pings for this roster
        start_dt = datetime.combine(roster.assigned_date, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(roster.assigned_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        gps_pings = (
            db.query(GpsTrackingPing)
            .filter(GpsTrackingPing.user_id == guard.user_id)
            .filter(GpsTrackingPing.roster_id == roster.roster_id)
            .filter(GpsTrackingPing.recorded_at >= start_dt)
            .filter(GpsTrackingPing.recorded_at < end_dt)
            .order_by(GpsTrackingPing.recorded_at)
            .all()
        )

        record = _build_employee_record(guard, roster, shift, site, logs, roster.assigned_date, gps_pings=gps_pings)
        employees.append(record)

    # Summary stats
    total = len(employees)
    present = sum(1 for e in employees if e["status"] == "present")
    late = sum(1 for e in employees if e["status"] == "late")
    absent = sum(1 for e in employees if e["status"] == "absent")
    total_deductions = round(sum(e["salary_deduction"] for e in employees), 2)

    return {
        "date": f"{date_from.isoformat()} to {date_to.isoformat()}",
        "employees": employees,
        "summary": {
            "total": total,
            "present": present,
            "late": late,
            "absent": absent,
            "total_deductions": total_deductions,
        },
    }


@router.get("/export", summary="Export workforce log as CSV")
def export_workforce_csv(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    role_filter: Optional[str] = Query(None),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Export workforce log for a date as CSV."""
    query = (
        db.query(GuardRoster)
        .options(
            joinedload(GuardRoster.guard),
            joinedload(GuardRoster.shift).joinedload(Shift.site),
            joinedload(GuardRoster.attendance_logs),
        )
        .filter(GuardRoster.assigned_date >= date_from)
        .filter(GuardRoster.assigned_date <= date_to)
        .filter(GuardRoster.status != "canceled")
    )
    rosters = query.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Date", "Name", "Role", "Site", "Shift", "Scheduled Hours",
        "Actual Hours", "First In", "Last Out", "Sessions", "Status",
        "Alerts", "Salary Deduction", "Deduction Reason"
    ])

    for roster in rosters:
        guard = roster.guard
        shift = roster.shift
        site = shift.site if shift else None

        if not guard or not shift:
            continue

        guard_role = guard.role.value if hasattr(guard.role, "value") else guard.role
        if role_filter and role_filter != "all":
            if guard_role != role_filter:
                continue

        logs = roster.attendance_logs or []

        # Fetch GPS pings for this roster
        start_dt = datetime.combine(roster.assigned_date, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(roster.assigned_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        gps_pings = (
            db.query(GpsTrackingPing)
            .filter(GpsTrackingPing.user_id == guard.user_id)
            .filter(GpsTrackingPing.roster_id == roster.roster_id)
            .filter(GpsTrackingPing.recorded_at >= start_dt)
            .filter(GpsTrackingPing.recorded_at < end_dt)
            .order_by(GpsTrackingPing.recorded_at)
            .all()
        )

        rec = _build_employee_record(guard, roster, shift, site, logs, roster.assigned_date, gps_pings=gps_pings)

        writer.writerow([
            roster.assigned_date.isoformat(),
            rec["name"],
            rec["role"],
            rec["site_name"],
            rec["shift_label"],
            rec["scheduled_hours"],
            rec["actual_hours"],
            rec["first_checkin"] or "",
            rec["last_checkout"] or "",
            rec["sessions_count"],
            rec["status"],
            "; ".join(rec["alerts"]) if rec["alerts"] else "",
            rec["salary_deduction"],
            rec["deduction_reason"],
        ])

    output.seek(0)
    filename = f"workforce_log_{date_from.isoformat()}_to_{date_to.isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
