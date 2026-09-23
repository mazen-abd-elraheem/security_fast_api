"""
SecureTrack Operations Room API
Real-time site attendance status, deficit alerts, color-coded indicators.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import date
from pydantic import BaseModel

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.site import Site
from app.models.shift import Shift
from app.models.guard_roster import GuardRoster
from app.models.daily_attendance_entry import DailyAttendanceEntry
from app.core.audit import log_audit, log_create, log_update, log_delete, log_read, snapshot

router = APIRouter()

class ShiftStatus(BaseModel):
    shift_id: str
    shift_label: str
    time: str
    required_headcount: int
    total_assigned: int
    total_present: int
    total_late: int
    total_absent: int
    deficit: int
    status_color: str
    coverage_percent: float

class SiteStatus(BaseModel):
    site_id: str
    site_name: str
    total_required: int
    total_assigned: int
    total_present: int
    total_late: int
    total_absent: int
    deficit: int
    status_color: str
    coverage_percent: float
    shifts: List[ShiftStatus]

class OperationsOverview(BaseModel):
    date: str
    total_sites: int
    sites_green: int
    sites_yellow: int
    sites_red: int
    total_guards_required: int
    total_guards_present: int
    overall_coverage: float
    sites: List[SiteStatus]

class DeficitAlert(BaseModel):
    site_id: str
    site_name: str
    deficit: int
    required: int
    present: int
    status_color: str

def _get_site_status(db: Session, site: Site, target_date: date) -> SiteStatus:
    shifts = db.query(Shift).filter(Shift.site_id == site.site_id, Shift.is_active == True).all()
    shift_ids = [s.shift_id for s in shifts]

    rosters = db.query(GuardRoster).filter(
        GuardRoster.shift_id.in_(shift_ids),
        GuardRoster.assigned_date == target_date,
        GuardRoster.status != "canceled"
    ).all()
    
    entries = db.query(DailyAttendanceEntry).filter(
        DailyAttendanceEntry.site_id == site.site_id,
        DailyAttendanceEntry.entry_date == target_date
    ).all()
    
    # Map employee_id -> entry
    entry_map = {e.employee_id: e for e in entries}

    # Identify replacements
    replaced_by_map = {}
    for entry in entries:
        if entry.replaced_by_id:
            replaced_by_map[entry.replaced_by_id] = entry.employee_id

    site_shifts = []
    
    for shift in shifts:
        shift_rosters = [r for r in rosters if r.shift_id == shift.shift_id]
        
        # We only count ORIGINAL guards towards 'required_headcount' vs 'present'
        # A replacement's attendance counts as the original guard's attendance.
        # Actually, let's just count all entries for the shift.
        
        present = 0
        late = 0
        absent = 0
        assigned = 0
        
        for r in shift_rosters:
            is_replacement = r.guard_id in replaced_by_map
            if not is_replacement:
                assigned += 1
                
            entry = entry_map.get(r.guard_id)
            if entry:
                if entry.status in ("present", "late", "rest_day_worked"):
                    present += 1
                elif entry.status == "absence_unexcused":
                    # Only count absence for original guards, a replacement shouldn't be absent, but if they are it's covered by original being absent
                    if not is_replacement:
                        absent += 1
                if entry.late_minutes and entry.late_minutes > 10:
                    late += 1
            else:
                # No entry yet
                if not is_replacement:
                    absent += 1

        required = shift.required_headcount or 0
        # If required = 2, and 2 assigned. Both present = 2 present.
        # If 1 absent, 1 replacement present. We have 2 assigned. absent=1 (for original), present=1 (for original) + 1 (for replacement) = 2.
        # Wait, if original is absent, present=1 (from original) is FALSE, present=0.
        # Replacement is present, present=1. Total present = 1.
        # But wait! If the original is 'absence_unexcused' (absent=1) AND replacement is 'present' (present=1),
        # Does that mean absent=1 AND present=2 (if the other guard is present)? Yes!
        # But `deficit` should be `required - present`.
        
        deficit = max(0, required - present)
        cov = (present / required * 100) if required > 0 else 100.0
        color = "green" if deficit == 0 else ("yellow" if cov >= 80 else "red")
        
        time_str = f"{shift.start_time.strftime('%H:%M')} - {shift.end_time.strftime('%H:%M')}" if shift.start_time and shift.end_time else "N/A"
        
        site_shifts.append(ShiftStatus(
            shift_id=shift.shift_id,
            shift_label=shift.label or "Unknown",
            time=time_str,
            required_headcount=required,
            total_assigned=assigned,
            total_present=present,
            total_late=late,
            total_absent=absent,
            deficit=deficit,
            status_color=color,
            coverage_percent=round(cov, 1)
        ))

    total_required = sum(s.required_headcount for s in site_shifts)
    total_assigned = sum(s.total_assigned for s in site_shifts)
    total_present = sum(s.total_present for s in site_shifts)
    total_late = sum(s.total_late for s in site_shifts)
    total_absent = sum(s.total_absent for s in site_shifts)
    
    deficit = max(0, total_required - total_present)
    cov = (total_present / total_required * 100) if total_required > 0 else 100.0
    color = "green" if deficit == 0 else ("yellow" if cov >= 80 else "red")
    
    return SiteStatus(
        site_id=site.site_id, 
        site_name=site.name, 
        total_required=int(total_required),
        total_assigned=int(total_assigned), 
        total_present=total_present, 
        total_late=total_late,
        total_absent=total_absent, 
        deficit=deficit, 
        status_color=color, 
        coverage_percent=round(cov, 1),
        shifts=site_shifts
    )

@router.get("/live-status", response_model=OperationsOverview)
def get_live_status(target_date: Optional[str] = None, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)):
    if current_user.role not in ("admin", "operations_manager", "CEO"):
        raise HTTPException(status_code=403, detail="Access denied")
    dt = date.fromisoformat(target_date) if target_date else date.today()
    sites = db.query(Site).all()
    ss = [_get_site_status(db, s, dt) for s in sites]
    tr = sum(s.total_required for s in ss)
    tp = sum(s.total_present for s in ss)
    ov = (tp / tr * 100) if tr > 0 else 100.0
    return OperationsOverview(date=dt.isoformat(), total_sites=len(sites),
        sites_green=sum(1 for s in ss if s.status_color == "green"),
        sites_yellow=sum(1 for s in ss if s.status_color == "yellow"),
        sites_red=sum(1 for s in ss if s.status_color == "red"),
        total_guards_required=tr, total_guards_present=tp, overall_coverage=round(ov, 1), sites=ss)

@router.get("/deficit-alerts", response_model=List[DeficitAlert])
def get_deficit_alerts(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role not in ("admin", "operations_manager", "CEO"):
        raise HTTPException(status_code=403, detail="Access denied")
    dt = date.today()
    sites = db.query(Site).all()
    alerts = []
    for site in sites:
        st = _get_site_status(db, site, dt)
        if st.deficit > 0:
            alerts.append(DeficitAlert(site_id=st.site_id, site_name=st.site_name, deficit=st.deficit,
                required=st.total_required, present=st.total_present, status_color=st.status_color))
    alerts.sort(key=lambda a: a.deficit, reverse=True)
    return alerts

@router.get("/site/{site_id}", response_model=SiteStatus)
def get_site_detail(site_id: str, target_date: Optional[str] = None, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)):
    if current_user.role not in ("admin", "operations_manager", "CEO", "leader"):
        raise HTTPException(status_code=403, detail="Access denied")
    site = db.query(Site).filter(Site.site_id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    dt = date.fromisoformat(target_date) if target_date else date.today()
    return _get_site_status(db, site, dt)
