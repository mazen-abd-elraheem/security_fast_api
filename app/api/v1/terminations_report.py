from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from datetime import datetime, date

from app.core.database import get_db
from app.api import deps
from app.api.deps import require_role
from app.enums import UserRole

from app.models.user import User
from app.models.clothes_inventory import ClothesTermination
from app.models.guard_roster import GuardRoster
from app.models.site import Site
from app.models.daily_attendance_entry import DailyAttendanceEntry
from app.models.disciplinary_action import DisciplinaryAction

router = APIRouter()

@router.get("/sheet")
def get_terminations_sheet(
    date_from: str = Query(..., description="Start date (YYYY-MM-DD)"),
    date_to: str = Query(..., description="End date (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Returns aggregated data for terminated employees.
    Scope of attendance and deductions is bounded by date_from and date_to.
    """
    try:
        dt_from = datetime.strptime(date_from, "%Y-%m-%d").date()
        dt_to = datetime.strptime(date_to, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    # 1. Fetch all inactive users (terminated)
    inactive_users = db.query(User).filter(User.is_active == False).all()

    # 2. Fetch Clothes Terminations mapped by user name / user id
    clothes_records = db.query(ClothesTermination).all()
    clothes_map = {c.user_name: c for c in clothes_records if c.user_name}

    results = []
    
    for u in inactive_users:
        user_id = u.user_id
        
        # Last assigned project & supervisor
        last_roster = (
            db.query(GuardRoster)
            .filter(GuardRoster.guard_id == user_id)
            .order_by(GuardRoster.shift_date.desc())
            .first()
        )
        
        last_project = "غير محدد"
        last_supervisor = "غير محدد"
        if last_roster:
            site = db.query(Site).filter(Site.site_id == last_roster.site_id).first()
            if site:
                last_project = site.name
            if last_roster.supervisor_id:
                sup = db.query(User).filter(User.user_id == last_roster.supervisor_id).first()
                if sup:
                    last_supervisor = sup.name

        # Uniform status & actual termination date
        c_record = clothes_map.get(u.name)
        term_date = u.updated_at.strftime("%Y-%m-%d")
        uniform_status = "غير محدد"
        received_by = "غير محدد"
        reason = "غير محدد"
        
        if c_record:
            if c_record.termination_date:
                term_date = c_record.termination_date.strftime("%Y-%m-%d")
            if c_record.clothes_status:
                uniform_status = c_record.clothes_status
            if c_record.received_by:
                received_by = c_record.received_by
            reason = c_record.reason or "غير محدد"
        elif u.uniform_status:
            uniform_status = u.uniform_status

        # Attendance totals for the date range
        attendance_entries = (
            db.query(DailyAttendanceEntry)
            .filter(
                DailyAttendanceEntry.employee_id == user_id,
                DailyAttendanceEntry.entry_date >= dt_from,
                DailyAttendanceEntry.entry_date <= dt_to
            )
            .all()
        )
        
        sick_leave = 0
        excused = 0
        unexcused = 0
        annual = 0
        rest = 0
        overtime = 0.0
        late = 0
        
        for entry in attendance_entries:
            st = entry.status
            if st == "sick_leave":
                sick_leave += 1
            elif st == "absence_excused":
                excused += 1
            elif st == "absence_unexcused":
                unexcused += 1
            elif st == "annual_leave":
                annual += 1
            elif st in ["rest", "rest_day_worked"]:
                rest += 1
                
            overtime += float(entry.overtime_hours)
            late += int(entry.late_minutes)

        # Deductions
        deductions = (
            db.query(func.sum(DisciplinaryAction.deduction_amount))
            .filter(
                DisciplinaryAction.guard_id == user_id,
                DisciplinaryAction.action_type == "deduction",
                DisciplinaryAction.status == "active",
                DisciplinaryAction.created_at >= datetime.combine(dt_from, datetime.min.time()),
                DisciplinaryAction.created_at <= datetime.combine(dt_to, datetime.max.time())
            )
            .scalar()
        ) or 0.0
        
        deduction_days = (
            db.query(func.sum(DisciplinaryAction.deduction_days))
            .filter(
                DisciplinaryAction.guard_id == user_id,
                DisciplinaryAction.action_type == "deduction",
                DisciplinaryAction.status == "active",
                DisciplinaryAction.created_at >= datetime.combine(dt_from, datetime.min.time()),
                DisciplinaryAction.created_at <= datetime.combine(dt_to, datetime.max.time())
            )
            .scalar()
        ) or 0

        total_deductions_val = deductions + (deduction_days * (u.daily_rate or 0))

        results.append({
            "user_id": user_id,
            "employee_code": u.employee_code or "-",
            "name": u.name,
            "classification": u.classification or "-",
            "project": last_project,
            "supervisor": last_supervisor,
            "hire_date": u.hire_date.strftime("%Y-%m-%d") if u.hire_date else "-",
            "termination_date": term_date,
            "reason": reason,
            "uniform_status": uniform_status,
            "received_by": received_by,
            "annual_leave": annual,
            "sick_leave": sick_leave,
            "excused_absence": excused,
            "unexcused_absence": unexcused,
            "overtime": overtime,
            "late": late,
            "deductions": total_deductions_val,
            "rest": rest,
        })

    return {"records": results}
