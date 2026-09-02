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
from app.models.daily_attendance_entry import DailyAttendanceEntry
from app.models.supervisor_visit import SupervisorVisit
from app.models.travel_fee import TravelFee
from app.enums import UserRole
from app.api.v1.tracking import compute_presence_hours_from_pings

router = APIRouter()

# ── Deduction Rules (same as workforce.py — single source in production) ──
LATE_THRESHOLD_MINUTES = 10
ABSENT_DEDUCTION = 200.0
LATE_DEDUCTION_PER_MINUTE = 2.0
EARLY_LEAVE_DEDUCTION = 100.0
NO_CHECKOUT_DEDUCTION = 50.0
OUTSIDE_GEOFENCE_DEDUCTION_PER_HOUR = 20.0  # Deduction per hour spent outside geofence during shift
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

    # Outside geofence deduction: track time spent outside during shift
    total_outside_seconds = sum((log.total_outside_seconds or 0) for log in logs)
    outside_hours = round(total_outside_seconds / 3600.0, 2) if total_outside_seconds > 0 else 0.0
    if outside_hours > 0:
        outside_deduction = round(outside_hours * OUTSIDE_GEOFENCE_DEDUCTION_PER_HOUR, 2)
        deductions.append({
            "reason": f"Outside geofence ({outside_hours}h)",
            "amount": outside_deduction,
        })

    total_deduction = round(sum(d["amount"] for d in deductions), 2)

    return {
        "date": target_date.isoformat(),
        "scheduled_hours": scheduled_hours,
        "actual_hours": actual_hours,
        "outside_hours": outside_hours,
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
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """
    Generate payroll report using DailyAttendanceEntry (supervisor's manual entries).
    """
    # 1. Fetch eligible users
    users_query = db.query(User).filter(User.is_active == True)
    if role_filter and role_filter != "all":
        users_query = users_query.filter(User.role == role_filter)
    else:
        users_query = users_query.filter(User.role.in_(["guard", "outdoor", "supervisor"]))
    
    users = users_query.all()
    user_dict = {u.user_id: u for u in users}

    if not users:
        return {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "currency": CURRENCY,
            "employees": [],
            "summary": {
                "total_employees": 0,
                "grand_total_deductions": 0.0,
                "grand_total_net_pay": 0.0,
            },
        }

    # 2. Get rosters (just to know scheduled days and default site)
    rosters = (
        db.query(GuardRoster)
        .options(joinedload(GuardRoster.shift).joinedload(Shift.site))
        .filter(GuardRoster.guard_id.in_(user_dict.keys()))
        .filter(GuardRoster.assigned_date >= date_from)
        .filter(GuardRoster.assigned_date <= date_to)
        .all()
    )

    user_rosters: dict[str, list] = {u_id: [] for u_id in user_dict.keys()}
    for roster in rosters:
        user_rosters[roster.guard_id].append(roster)

    # 3. Get Daily Attendance Entries (source of truth)
    entries = (
        db.query(DailyAttendanceEntry)
        .filter(DailyAttendanceEntry.employee_id.in_(user_dict.keys()))
        .filter(DailyAttendanceEntry.entry_date >= date_from)
        .filter(DailyAttendanceEntry.entry_date <= date_to)
        .all()
    )

    user_entries: dict[str, list] = {u_id: [] for u_id in user_dict.keys()}
    for entry in entries:
        user_entries[entry.employee_id].append(entry)

    employees = []
    for user_id, user in user_dict.items():
        user_roster_list = user_rosters[user_id]
        user_entry_list = user_entries[user_id]
        
        base_salary = getattr(user, "base_salary", None) or 0.0

        days_present = sum(1 for e in user_entry_list if e.status == 'present')
        days_absent_unexcused = sum(1 for e in user_entry_list if e.status == 'absence_unexcused')
        days_absent_excused = sum(1 for e in user_entry_list if e.status == 'absence_excused')
        days_late = sum(1 for e in user_entry_list if e.late_minutes > LATE_THRESHOLD_MINUTES)
        
        advances = sum(e.advance_amount for e in user_entry_list)
        total_overtime = sum(e.overtime_hours for e in user_entry_list)
        
        # Calculate deductions based on entries
        deductions = []
        if days_absent_unexcused > 0:
            deductions.append({"reason": "Absence (Unexcused)", "amount": round(days_absent_unexcused * ABSENT_DEDUCTION, 2)})
            
        late_deduction = 0.0
        for e in user_entry_list:
            if e.late_minutes > LATE_THRESHOLD_MINUTES:
                late_deduction += e.late_minutes * LATE_DEDUCTION_PER_MINUTE
        if late_deduction > 0:
            deductions.append({"reason": "Late arrival", "amount": round(late_deduction, 2)})
            
        total_deduction = round(sum(d["amount"] for d in deductions), 2)
        net_pay = round(base_salary - total_deduction - advances, 2)

        employees.append({
            "user_id": user.user_id,
            "name": user.name,
            "role": user.role.value if hasattr(user.role, "value") else user.role,
            "badge_number": user.badge_number,
            "bank_account": getattr(user, "bank_account", "") or "",
            "base_salary": base_salary,
            "days_present": days_present,
            "days_absent": days_absent_unexcused + days_absent_excused,
            "days_late": days_late,
            "total_days": len(user_entry_list),
            "total_deductions": total_deduction,
            "deduction_breakdown": deductions,
            "advances": advances,
            "net_pay": net_pay,
            "currency": CURRENCY,
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
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """Export payroll report as CSV with 21 Arabic columns."""
    users_query = db.query(User).filter(User.is_active == True)
    if role_filter and role_filter != "all":
        users_query = users_query.filter(User.role == role_filter)
    else:
        users_query = users_query.filter(User.role.in_(["guard", "outdoor", "supervisor"]))
    
    users = users_query.all()
    user_dict = {u.user_id: u for u in users}

    headers = [
        "الاكواد", "مسلسل", "التصنيف", "توقيت\nالعمل", "المشرف", "مشروع", 
        "تاريخ \nالتعيين", "الاســـــــــــــــــــــــــــــم", "غياب \nباذن", 
        "غياب \nبدون", "اضافى", "بدل \nراحه", "تاخير", "خصم", "راحة", 
        "اجازة \nمن \nالسنوي", "اجازة\nمرضي", "ايام \nالعمل \nالتشغيليه", 
        "الاجر\nاليومية", "بدل \nانتقالات", "اجمالي الراتب", "السلف"
    ]

    output = io.StringIO()
    output.write('\ufeff')  # UTF-8 BOM
    writer = csv.writer(output)
    writer.writerow(headers)

    if not users:
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=payroll_{date_from}_to_{date_to}.csv"}
        )

    rosters = (
        db.query(GuardRoster)
        .options(joinedload(GuardRoster.shift).joinedload(Shift.site))
        .filter(GuardRoster.guard_id.in_(user_dict.keys()))
        .filter(GuardRoster.assigned_date >= date_from)
        .filter(GuardRoster.assigned_date <= date_to)
        .all()
    )

    user_rosters: dict[str, list] = {u_id: [] for u_id in user_dict.keys()}
    for roster in rosters:
        user_rosters[roster.guard_id].append(roster)

    entries = (
        db.query(DailyAttendanceEntry)
        .filter(DailyAttendanceEntry.employee_id.in_(user_dict.keys()))
        .filter(DailyAttendanceEntry.entry_date >= date_from)
        .filter(DailyAttendanceEntry.entry_date <= date_to)
        .all()
    )

    user_entries: dict[str, list] = {u_id: [] for u_id in user_dict.keys()}
    for entry in entries:
        user_entries[entry.employee_id].append(entry)

    # To get supervisor names for entries
    sup_ids = {e.entered_by for e in entries}
    supervisors = db.query(User).filter(User.user_id.in_(sup_ids)).all()
    sup_dict = {s.user_id: s.name for s in supervisors}
    
    # To get site names
    sites = db.query(Site).all()
    site_dict = {s.site_id: s.name for s in sites}
    
    # Pre-fetch Base site and Travel Fees for calculation
    base_site = next((s for s in sites if getattr(s, 'is_base', False)), None)
    base_site_name = base_site.name if base_site else "Base"
    travel_fees = db.query(TravelFee).filter(TravelFee.is_active == True).all()
    travel_fee_map = {(tf.from_site_name.lower(), tf.to_site_name.lower()): float(tf.amount) for tf in travel_fees}

    for idx, (user_id, user) in enumerate(user_dict.items(), start=1):
        r_list = user_rosters[user_id]
        e_list = user_entries[user_id]
        
        base_salary = getattr(user, "base_salary", None) or 0.0
        daily_rate = round(base_salary / 30, 2) if base_salary else 0.0

        days_excused = sum(1 for e in e_list if e.status == 'absence_excused')
        days_unexcused = sum(1 for e in e_list if e.status == 'absence_unexcused')
        days_present = sum(1 for e in e_list if e.status == 'present')
        days_rest = sum(1 for e in e_list if e.status == 'rest')
        days_rest_worked = sum(1 for e in e_list if e.status == 'rest_day_worked')
        days_annual = sum(1 for e in e_list if e.status == 'annual_leave')
        days_sick = sum(1 for e in e_list if e.status == 'sick_leave')
        days_late = sum(1 for e in e_list if e.late_minutes > LATE_THRESHOLD_MINUTES)
        
        overtime_hours = sum(e.overtime_hours for e in e_list)
        advances = sum(e.advance_amount for e in e_list)
        
        # Site / Shift / Supervisor logic
        primary_site = "N/A"
        shift_label = "N/A"
        if r_list:
            if r_list[0].shift and r_list[0].shift.site:
                primary_site = r_list[0].shift.site.name
            shift_label = r_list[0].shift.label if r_list[0].shift else "N/A"
        elif e_list:
            primary_site = site_dict.get(e_list[-1].site_id, "N/A")

        primary_sup = "N/A"
        if e_list:
            primary_sup = sup_dict.get(e_list[-1].entered_by, "N/A")

        # Deductions
        late_deduction = sum((e.late_minutes * LATE_DEDUCTION_PER_MINUTE) for e in e_list if e.late_minutes > LATE_THRESHOLD_MINUTES)
        absent_deduction = days_unexcused * ABSENT_DEDUCTION
        total_deductions = round(late_deduction + absent_deduction, 2)

        travel_allowance = 0.0
        role_str = user.role.value if hasattr(user.role, "value") else user.role
        if role_str in ['supervisor', 'leader']:
            visits = (
                db.query(SupervisorVisit)
                .options(joinedload(SupervisorVisit.site))
                .filter(
                    SupervisorVisit.supervisor_id == user.user_id,
                    SupervisorVisit.check_in_time >= datetime.combine(date_from, datetime.min.time()),
                    SupervisorVisit.check_in_time <= datetime.combine(date_to, datetime.max.time()),
                    SupervisorVisit.is_verified == True
                )
                .order_by(SupervisorVisit.check_in_time.asc())
                .all()
            )
            visits_by_date = {}
            for v in visits:
                d = v.check_in_time.date()
                if d not in visits_by_date:
                    visits_by_date[d] = []
                visits_by_date[d].append(v)
            for d, daily_visits in visits_by_date.items():
                if not daily_visits:
                    continue
                first_site = daily_visits[0].site.name
                travel_allowance += travel_fee_map.get((base_site_name.lower(), first_site.lower()), 0.0)
                for i in range(1, len(daily_visits)):
                    from_s = daily_visits[i-1].site.name
                    to_s = daily_visits[i].site.name
                    travel_allowance += travel_fee_map.get((from_s.lower(), to_s.lower()), 0.0)
                last_site = daily_visits[-1].site.name
                travel_allowance += travel_fee_map.get((last_site.lower(), base_site_name.lower()), 0.0)
                
        net_pay = round(base_salary + travel_allowance - total_deductions - advances, 2)

        # 1. "الاكواد"
        # 2. "مسلسل"
        # 3. "التصنيف"
        # 4. "توقيت\nالعمل"
        # 5. "المشرف"
        # 6. "مشروع"
        # 7. "تاريخ \nالتعيين"
        # 8. "الاســـــــــــــــــــــــــــــم"
        # 9. "غياب \nباذن"
        # 10. "غياب \nبدون"
        # 11. "اضافى"
        # 12. "بدل \nراحه"
        # 13. "تاخير"
        # 14. "خصم"
        # 15. "راحة"
        # 16. "اجازة \nمن \nالسنوي"
        # 17. "اجازة\nمرضي"
        # 18. "ايام \nالعمل \nالتشغيليه"
        # 19. "الاجر\nاليومية"
        # 20. "اجمالي الراتب"
        # 21. "السلف"
        hire_date_str = user.hire_date.strftime('%Y-%m-%d') if user.hire_date else (user.created_at.strftime('%Y-%m-%d') if user.created_at else "N/A")

        writer.writerow([
            user.badge_number or "N/A",  # الاكواد
            idx,  # مسلسل
            role_str,  # التصنيف
            shift_label,  # توقيت العمل
            primary_sup,  # المشرف
            primary_site,  # مشروع
            hire_date_str,  # تاريخ التعيين
            user.name,  # الاسم
            days_excused,  # غياب باذن
            days_unexcused,  # غياب بدون
            round(overtime_hours, 2),  # اضافى
            days_rest_worked,  # بدل راحه
            days_late,  # تاخير
            total_deductions,  # خصم
            days_rest,  # راحة
            days_annual,  # اجازة سنوي
            days_sick,  # اجازة مرضي
            len(r_list) if len(r_list) > 0 else len(e_list),  # التشغيليه
            daily_rate,  # الاجر اليومية
            round(travel_allowance, 2),  # بدل انتقالات
            net_pay,  # اجمالي الراتب
            advances  # السلف
        ])

    output.seek(0)
    filename = f"payroll_{date_from.isoformat()}_to_{date_to.isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

@router.get("/export-bank", summary="Export Bank Payroll as CSV")
def export_bank_payroll_csv(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    role_filter: Optional[str] = Query(None),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """Export Bank Payroll report as CSV."""
    users_query = db.query(User).filter(User.is_active == True)
    if role_filter and role_filter != "all":
        users_query = users_query.filter(User.role == role_filter)
    else:
        users_query = users_query.filter(User.role.in_(["guard", "outdoor", "supervisor"]))
    
    users = users_query.all()
    user_dict = {u.user_id: u for u in users}

    # As requested by the user:
    headers = ["الكود", "الاسم", "رقم الحساب", "المبلغ"]

    output = io.StringIO()
    output.write('\ufeff')  # UTF-8 BOM
    writer = csv.writer(output)
    writer.writerow(headers)

    if not users:
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=bank_payroll_{date_from}_to_{date_to}.csv"}
        )

    entries = (
        db.query(DailyAttendanceEntry)
        .filter(DailyAttendanceEntry.employee_id.in_(user_dict.keys()))
        .filter(DailyAttendanceEntry.entry_date >= date_from)
        .filter(DailyAttendanceEntry.entry_date <= date_to)
        .all()
    )

    user_entries: dict[str, list] = {u_id: [] for u_id in user_dict.keys()}
    for entry in entries:
        user_entries[entry.employee_id].append(entry)

    sites = db.query(Site).all()
    base_site = next((s for s in sites if getattr(s, 'is_base', False)), None)
    base_site_name = base_site.name if base_site else "Base"
    travel_fees = db.query(TravelFee).filter(TravelFee.is_active == True).all()
    travel_fee_map = {(tf.from_site_name.lower(), tf.to_site_name.lower()): float(tf.amount) for tf in travel_fees}

    total_sum = 0.0

    for user_id, user in user_dict.items():
        e_list = user_entries[user_id]
        
        base_salary = getattr(user, "base_salary", None) or 0.0
        days_unexcused = sum(1 for e in e_list if e.status == 'absence_unexcused')
        advances = sum(e.advance_amount for e in e_list)
        
        late_deduction = sum((e.late_minutes * LATE_DEDUCTION_PER_MINUTE) for e in e_list if e.late_minutes > LATE_THRESHOLD_MINUTES)
        absent_deduction = days_unexcused * ABSENT_DEDUCTION
        total_deductions = round(late_deduction + absent_deduction, 2)

        travel_allowance = 0.0
        role_str = user.role.value if hasattr(user.role, "value") else user.role
        if role_str in ['supervisor', 'leader']:
            visits = (
                db.query(SupervisorVisit)
                .options(joinedload(SupervisorVisit.site))
                .filter(
                    SupervisorVisit.supervisor_id == user.user_id,
                    SupervisorVisit.check_in_time >= datetime.combine(date_from, datetime.min.time()),
                    SupervisorVisit.check_in_time <= datetime.combine(date_to, datetime.max.time()),
                    SupervisorVisit.is_verified == True
                )
                .order_by(SupervisorVisit.check_in_time.asc())
                .all()
            )
            visits_by_date = {}
            for v in visits:
                d = v.check_in_time.date()
                if d not in visits_by_date:
                    visits_by_date[d] = []
                visits_by_date[d].append(v)
            for d, daily_visits in visits_by_date.items():
                if not daily_visits:
                    continue
                first_site = daily_visits[0].site.name
                travel_allowance += travel_fee_map.get((base_site_name.lower(), first_site.lower()), 0.0)
                for i in range(1, len(daily_visits)):
                    from_s = daily_visits[i-1].site.name
                    to_s = daily_visits[i].site.name
                    travel_allowance += travel_fee_map.get((from_s.lower(), to_s.lower()), 0.0)
                last_site = daily_visits[-1].site.name
                travel_allowance += travel_fee_map.get((last_site.lower(), base_site_name.lower()), 0.0)
                
        net_pay = round(base_salary + travel_allowance - total_deductions - advances, 2)
        total_sum += net_pay

        writer.writerow([
            user.badge_number or "N/A",  # الكود
            user.name,  # الاسم
            getattr(user, "bank_account", "") or "",  # رقم الحساب
            net_pay  # المبلغ
        ])

    # Footer row
    writer.writerow(["", "", "اجمالي المرتبات", round(total_sum, 2)])

    output.seek(0)
    filename = f"bank_payroll_{date_from.isoformat()}_to_{date_to.isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
@router.put("/salary/{user_id}", summary="Update base salary")
def update_salary(
    user_id: str,
    base_salary: float = Query(..., ge=0, description="New base salary in EGP"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
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

@router.put("/bank-account/{user_id}", summary="Update bank account number")
def update_bank_account(
    user_id: str,
    bank_account: str = Query(..., description="New bank account number"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """Update a user's bank account number."""
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found")

    user.bank_account = bank_account
    db.commit()

    return {
        "detail": "Bank account updated successfully",
        "user_id": user_id,
        "bank_account": bank_account,
    }
