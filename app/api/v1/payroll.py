"""
SecureTrack Platform — Payroll Routes
Admin payroll reports, salary management, and CSV export.
"""
import csv
import io
from datetime import date, datetime, timedelta, timezone, time as dtime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload

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

# ── Deduction Rules (same as workforce.py — single source in production) ──
LATE_THRESHOLD_MINUTES = 10
ABSENT_DEDUCTION = 200.0
LATE_DEDUCTION_PER_MINUTE = 2.0
EARLY_LEAVE_DEDUCTION = 100.0
NO_CHECKOUT_DEDUCTION = 50.0
CURRENCY = "EGP"


def _time_to_minutes(t: dtime) -> float:
    return t.hour * 60 + t.minute + t.second / 60


def _compute_daily_record(
    user: User,
    roster: GuardRoster,
    shift: Shift,
    site: Site,
    logs: list[AttendanceLog],
    pings: list[GpsTrackingPing],
    target_date: date,
) -> dict:
    """Compute a single day's payroll data for one employee."""
    # Scheduled hours
    start_mins = _time_to_minutes(shift.start_time)
    end_mins = _time_to_minutes(shift.end_time)
    if end_mins <= start_mins:
        end_mins += 24 * 60
    scheduled_hours = round((end_mins - start_mins) / 60, 2)

    # Actual hours from GPS pings (preferred) or checkin/checkout (fallback)
    if pings:
        actual_hours = compute_presence_hours_from_pings(pings)
    else:
        # Fallback to checkin/checkout
        total = 0.0
        for log in logs:
            if log.recorded_at and log.checkout_at:
                cin = log.recorded_at
                cout = log.checkout_at
                if cin.tzinfo is None:
                    cin = cin.replace(tzinfo=timezone.utc)
                if cout.tzinfo is None:
                    cout = cout.replace(tzinfo=timezone.utc)
                diff = (cout - cin).total_seconds() / 3600.0
                if diff > 0:
                    total += diff
        actual_hours = round(total, 2)

    # Status and deductions
    deductions = []
    status = "present"

    if not logs and not pings:
        status = "absent"
        deductions.append({"reason": "Absent", "amount": ABSENT_DEDUCTION})
    elif logs:
        first_checkin = min(l.recorded_at for l in logs)
        if first_checkin.tzinfo is None:
            first_checkin = first_checkin.replace(tzinfo=timezone.utc)

        scheduled_dt = datetime.combine(target_date, shift.start_time, tzinfo=timezone.utc)
        late_minutes = (first_checkin - scheduled_dt).total_seconds() / 60

        if late_minutes > LATE_THRESHOLD_MINUTES:
            status = "late"
            deductions.append({
                "reason": f"Late arrival ({int(late_minutes)}m)",
                "amount": round(late_minutes * LATE_DEDUCTION_PER_MINUTE, 2),
            })

        # Check for missing checkout / early leave
        scheduled_end_dt = datetime.combine(target_date, shift.end_time, tzinfo=timezone.utc)
        if shift.end_time < shift.start_time:
            scheduled_end_dt += timedelta(days=1)

        now_utc = datetime.now(timezone.utc)
        if now_utc >= scheduled_end_dt:
            has_checkout = any(l.checkout_at for l in logs)
            if not has_checkout:
                deductions.append({"reason": "No checkout", "amount": NO_CHECKOUT_DEDUCTION})
            else:
                last_checkout = max(
                    (l.checkout_at for l in logs if l.checkout_at),
                    default=None,
                )
                if last_checkout:
                    if last_checkout.tzinfo is None:
                        last_checkout = last_checkout.replace(tzinfo=timezone.utc)
                    early_mins = (scheduled_end_dt - last_checkout).total_seconds() / 60
                    if early_mins > LATE_THRESHOLD_MINUTES:
                        deductions.append({"reason": "Early departure", "amount": EARLY_LEAVE_DEDUCTION})
    elif pings and not logs:
        # Has GPS pings but no formal checkin — use GPS presence
        in_fence = [p for p in pings if p.is_within_geofence]
        if not in_fence:
            status = "absent"
            deductions.append({"reason": "Absent (no geofence presence)", "amount": ABSENT_DEDUCTION})

    total_deduction = round(sum(d["amount"] for d in deductions), 2)

    return {
        "date": target_date.isoformat(),
        "scheduled_hours": scheduled_hours,
        "actual_hours": actual_hours,
        "status": status,
        "deductions": deductions,
        "total_deduction": total_deduction,
        "site_name": site.name if site else "Unknown",
        "shift_label": shift.label or f"{shift.start_time}-{shift.end_time}",
    }


@router.get("/report", summary="Payroll report for date range")
def get_payroll_report(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    role_filter: Optional[str] = Query(None, description="Filter by role: guard, outdoor, or all"),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Generate payroll report for all guards/outdoor in a date range.
    Aggregates daily attendance, GPS presence hours, and deductions.
    """
    # Get all roster entries in date range
    rosters = (
        db.query(GuardRoster)
        .options(
            joinedload(GuardRoster.guard),
            joinedload(GuardRoster.shift).joinedload(Shift.site),
            joinedload(GuardRoster.attendance_logs),
        )
        .filter(GuardRoster.assigned_date >= date_from)
        .filter(GuardRoster.assigned_date <= date_to)
        .all()
    )

    # Group rosters by user
    user_rosters: dict[str, list] = {}
    for roster in rosters:
        guard = roster.guard
        if not guard or not roster.shift:
            continue
        guard_role = guard.role.value if hasattr(guard.role, "value") else guard.role
        if role_filter and role_filter != "all" and guard_role != role_filter:
            continue
        user_rosters.setdefault(guard.user_id, []).append(roster)

    # Build per-employee payroll records
    employees = []
    for user_id, user_roster_list in user_rosters.items():
        guard = user_roster_list[0].guard
        base_salary = getattr(guard, "base_salary", None) or 0.0

        total_scheduled = 0.0
        total_actual = 0.0
        days_present = 0
        days_absent = 0
        days_late = 0
        all_deductions = []
        daily_records = []

        for roster in user_roster_list:
            shift = roster.shift
            site = shift.site if shift else None
            logs = roster.attendance_logs or []
            target_date = roster.assigned_date

            # Get GPS pings for this roster
            start_dt = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
            end_dt = datetime.combine(target_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
            pings = (
                db.query(GpsTrackingPing)
                .filter(GpsTrackingPing.user_id == user_id)
                .filter(GpsTrackingPing.roster_id == roster.roster_id)
                .filter(GpsTrackingPing.recorded_at >= start_dt)
                .filter(GpsTrackingPing.recorded_at < end_dt)
                .order_by(GpsTrackingPing.recorded_at)
                .all()
            )

            day_record = _compute_daily_record(guard, roster, shift, site, logs, pings, target_date)
            daily_records.append(day_record)

            total_scheduled += day_record["scheduled_hours"]
            total_actual += day_record["actual_hours"]

            if day_record["status"] == "present":
                days_present += 1
            elif day_record["status"] == "absent":
                days_absent += 1
            elif day_record["status"] == "late":
                days_late += 1

            for d in day_record["deductions"]:
                all_deductions.append({
                    "date": target_date.isoformat(),
                    "reason": d["reason"],
                    "amount": d["amount"],
                })

        total_deductions = round(sum(d["amount"] for d in all_deductions), 2)
        net_pay = round(base_salary - total_deductions, 2)

        employees.append({
            "user_id": guard.user_id,
            "name": guard.name,
            "role": guard.role.value if hasattr(guard.role, "value") else guard.role,
            "badge_number": guard.badge_number,
            "base_salary": base_salary,
            "total_scheduled_hours": round(total_scheduled, 2),
            "total_actual_hours": round(total_actual, 2),
            "days_present": days_present,
            "days_absent": days_absent,
            "days_late": days_late,
            "total_days": len(daily_records),
            "total_deductions": total_deductions,
            "deduction_breakdown": all_deductions,
            "net_pay": net_pay,
            "currency": CURRENCY,
            "daily_records": daily_records,
        })

    # Summary
    total_employees = len(employees)
    grand_total_deductions = round(sum(e["total_deductions"] for e in employees), 2)
    grand_total_net = round(sum(e["net_pay"] for e in employees), 2)

    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "currency": CURRENCY,
        "employees": employees,
        "summary": {
            "total_employees": total_employees,
            "grand_total_deductions": grand_total_deductions,
            "grand_total_net_pay": grand_total_net,
        },
    }


@router.get("/export", summary="Export payroll as CSV")
def export_payroll_csv(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    role_filter: Optional[str] = Query(None),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Export payroll report as CSV."""
    # Reuse the report logic
    from app.api.v1.payroll import get_payroll_report as _report
    # Build a mock for reuse — just call the same logic inline
    rosters = (
        db.query(GuardRoster)
        .options(
            joinedload(GuardRoster.guard),
            joinedload(GuardRoster.shift).joinedload(Shift.site),
            joinedload(GuardRoster.attendance_logs),
        )
        .filter(GuardRoster.assigned_date >= date_from)
        .filter(GuardRoster.assigned_date <= date_to)
        .all()
    )

    user_rosters: dict[str, list] = {}
    for roster in rosters:
        guard = roster.guard
        if not guard or not roster.shift:
            continue
        guard_role = guard.role.value if hasattr(guard.role, "value") else guard.role
        if role_filter and role_filter != "all" and guard_role != role_filter:
            continue
        user_rosters.setdefault(guard.user_id, []).append(roster)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Name", "Badge", "Role", "Base Salary (EGP)",
        "Days Present", "Days Late", "Days Absent", "Total Days",
        "Scheduled Hours", "Actual Hours",
        "Total Deductions (EGP)", "Net Pay (EGP)", "Deduction Details",
    ])

    for user_id, user_roster_list in user_rosters.items():
        guard = user_roster_list[0].guard
        base_salary = getattr(guard, "base_salary", None) or 0.0

        total_scheduled = 0.0
        total_actual = 0.0
        days_present = 0
        days_absent = 0
        days_late = 0
        all_deductions = []

        for roster in user_roster_list:
            shift = roster.shift
            site = shift.site if shift else None
            logs = roster.attendance_logs or []
            target_date = roster.assigned_date

            start_dt = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
            end_dt = datetime.combine(target_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
            pings = (
                db.query(GpsTrackingPing)
                .filter(GpsTrackingPing.user_id == user_id)
                .filter(GpsTrackingPing.roster_id == roster.roster_id)
                .filter(GpsTrackingPing.recorded_at >= start_dt)
                .filter(GpsTrackingPing.recorded_at < end_dt)
                .order_by(GpsTrackingPing.recorded_at)
                .all()
            )

            day_record = _compute_daily_record(guard, roster, shift, site, logs, pings, target_date)
            total_scheduled += day_record["scheduled_hours"]
            total_actual += day_record["actual_hours"]

            if day_record["status"] == "present":
                days_present += 1
            elif day_record["status"] == "absent":
                days_absent += 1
            elif day_record["status"] == "late":
                days_late += 1

            for d in day_record["deductions"]:
                all_deductions.append(f"{target_date}: {d['reason']} ({d['amount']})")

        total_deductions = round(sum(
            d["total_deduction"]
            for roster in user_roster_list
            for d in [_compute_daily_record(
                guard, roster, roster.shift, roster.shift.site if roster.shift else None,
                roster.attendance_logs or [],
                db.query(GpsTrackingPing)
                .filter(GpsTrackingPing.user_id == user_id)
                .filter(GpsTrackingPing.roster_id == roster.roster_id)
                .all(),
                roster.assigned_date,
            )]
        ), 2)
        net_pay = round(base_salary - total_deductions, 2)

        writer.writerow([
            guard.name,
            guard.badge_number or "",
            guard.role.value if hasattr(guard.role, "value") else guard.role,
            base_salary,
            days_present,
            days_late,
            days_absent,
            len(user_roster_list),
            round(total_scheduled, 2),
            round(total_actual, 2),
            total_deductions,
            net_pay,
            "; ".join(all_deductions) if all_deductions else "None",
        ])

    output.seek(0)
    filename = f"payroll_{date_from.isoformat()}_to_{date_to.isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.put("/salary/{user_id}", summary="Update base salary")
def update_salary(
    user_id: str,
    base_salary: float = Query(..., ge=0, description="New base salary in EGP"),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Update a user's base salary."""
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found")

    user.base_salary = base_salary
    db.commit()

    return {
        "detail": f"Salary updated to {base_salary} EGP",
        "user_id": user_id,
        "base_salary": base_salary,
    }
