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
from app.models.attendance_log import AttendanceLog
from app.core.audit import log_audit, log_create, log_update, log_delete, log_read, snapshot

router = APIRouter()

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
    total_required = db.query(func.coalesce(func.sum(Shift.required_headcount), 0)).filter(
        Shift.site_id == site.site_id, Shift.is_active == True).scalar() or 0
    today_shifts = db.query(Shift.shift_id).filter(
        Shift.site_id == site.site_id, Shift.is_active == True).subquery()
    total_assigned = db.query(func.count(GuardRoster.roster_id)).filter(
        GuardRoster.shift_id.in_(today_shifts), GuardRoster.assigned_date == target_date,
        GuardRoster.status == "assigned").scalar() or 0
    today_roster_ids = db.query(GuardRoster.roster_id).filter(
        GuardRoster.shift_id.in_(today_shifts), GuardRoster.assigned_date == target_date).subquery()
    attendance_counts = db.query(AttendanceLog.status, func.count(AttendanceLog.log_id)).filter(
        AttendanceLog.roster_id.in_(today_roster_ids)).group_by(AttendanceLog.status).all()
    counts = {s: c for s, c in attendance_counts}
    total_present = counts.get("present", 0) + counts.get("late", 0)
    total_late = counts.get("late", 0)
    total_absent = counts.get("absent", 0)
    eff = max(total_required, total_assigned)
    deficit = max(0, eff - total_present)
    cov = (total_present / eff * 100) if eff > 0 else 100.0
    color = "green" if deficit == 0 else ("yellow" if cov >= 80 else "red")
    return SiteStatus(site_id=site.site_id, site_name=site.name, total_required=int(total_required),
        total_assigned=int(total_assigned), total_present=total_present, total_late=total_late,
        total_absent=total_absent, deficit=deficit, status_color=color, coverage_percent=round(cov, 1))

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
