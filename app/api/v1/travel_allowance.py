"""
SecureTrack — Travel Allowance API
Manual submission by supervisors + ops-manager approval.
Admin/Accountant/CEO/OpsManager can view, edit, and export.
"""
import uuid
import csv
import io
from datetime import datetime, date, timezone
from typing import Optional
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import require_role, get_current_user
from app.models.travel_allowance_entry import TravelAllowanceEntry
from app.models.site import Site
from app.models.user import User
from app.enums import UserRole

router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────────────────────

class TravelAllowanceSubmit(BaseModel):
    """Supervisor submits a manual travel allowance request."""
    from_site: str = Field(..., description="Origin: 'home' or a site name")
    to_site: str = Field(..., description="Destination site name (auto-detected via GPS)")
    to_site_id: Optional[str] = Field(None, description="Destination site UUID for reference")
    amount: float = Field(..., ge=0, description="Amount in EGP entered manually by supervisor")
    notes: Optional[str] = None


class TravelAllowanceUpdate(BaseModel):
    amount: Optional[float] = None
    notes: Optional[str] = None
    from_site: Optional[str] = None
    to_site: Optional[str] = None
    status: Optional[str] = None
    approval_notes: Optional[str] = None


class TravelAllowanceCreate(BaseModel):
    """Admin manual creation."""
    supervisor_id: str
    trip_date: date
    from_site: str
    to_site: str
    amount: float = 0.0
    notes: Optional[str] = None


class ApprovalAction(BaseModel):
    action: str  # 'approve' or 'reject'
    approval_notes: Optional[str] = None


# ── Helper ────────────────────────────────────────────────────────────────────

def _next_trip_number(db: Session, trip_date: date) -> str:
    prefix = f"T-{trip_date.strftime('%Y%m%d')}-"
    existing = db.query(TravelAllowanceEntry).filter(
        TravelAllowanceEntry.trip_date == trip_date
    ).count()
    return f"{prefix}{existing + 1:03d}"


def _entry_to_dict(e: TravelAllowanceEntry, sup: Optional[User] = None) -> dict:
    return {
        "entry_id": e.entry_id,
        "supervisor_id": e.supervisor_id,
        "supervisor_name": sup.name if sup else None,
        "supervisor_badge": sup.badge_number if sup else None,
        "trip_date": e.trip_date.isoformat() if e.trip_date else None,
        "trip_number": e.trip_number,
        "from_site": e.from_site,
        "to_site": e.to_site,
        "amount": e.amount,
        "notes": e.notes or "",
        "status": getattr(e, "status", "pending"),
        "approval_notes": getattr(e, "approval_notes", None) or "",
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


# ── GET /sites — list all sites (for 'to' selection fallback) ─────────────────

@router.get("/sites", summary="List all sites for travel allowance destination selection")
def list_sites(
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.LEADER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Returns all active sites with name, lat, lng for GPS nearest-site matching."""
    sites = db.query(Site).filter(Site.status == "active").order_by(Site.name).all()
    return [
        {
            "site_id": s.site_id,
            "name": s.name,
            "latitude": getattr(s, "latitude", None),
            "longitude": getattr(s, "longitude", None),
        }
        for s in sites
    ]


# ── POST /submit — Supervisor submits ─────────────────────────────────────────

@router.post("/submit", status_code=201, summary="Supervisor submits a travel allowance request")
def submit_travel_allowance(
    data: TravelAllowanceSubmit,
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    """
    Supervisor manually submits a travel allowance:
    - from_site: 'home' or a site name
    - to_site: nearest site auto-detected by client GPS
    - amount: supervisor enters manually
    Status starts as 'pending', awaiting ops-manager approval.
    """
    today = date.today()
    trip_num = _next_trip_number(db, today)
    entry = TravelAllowanceEntry(
        entry_id=str(uuid.uuid4()),
        supervisor_id=current_user.user_id,
        trip_date=today,
        trip_number=trip_num,
        from_site=data.from_site,
        to_site=data.to_site,
        amount=data.amount,
        notes=data.notes,
        is_active=True,
        status="pending",
        submitted_by=current_user.user_id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _entry_to_dict(entry, current_user)


# ── GET /my — Supervisor views own history ────────────────────────────────────

@router.get("/my", summary="Supervisor views their own travel allowance history")
def my_travel_allowances(
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    entries = (
        db.query(TravelAllowanceEntry)
        .filter(
            TravelAllowanceEntry.supervisor_id == current_user.user_id,
            TravelAllowanceEntry.is_active == True,
        )
        .order_by(TravelAllowanceEntry.trip_date.desc())
        .all()
    )
    return [_entry_to_dict(e, current_user) for e in entries]


# ── POST /{entry_id}/approve — Ops Manager approves/rejects ──────────────────

@router.post("/{entry_id}/approve", summary="Ops Manager approves or rejects a travel allowance")
def approve_entry(
    entry_id: str,
    data: ApprovalAction,
    current_user: User = Depends(require_role(
        UserRole.OPERATIONS_MANAGER, UserRole.ADMIN
    )),
    db: Session = Depends(get_db),
):
    entry = db.query(TravelAllowanceEntry).filter(
        TravelAllowanceEntry.entry_id == entry_id,
        TravelAllowanceEntry.is_active == True,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if data.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'reject'")

    entry.status = "approved" if data.action == "approve" else "rejected"
    entry.approved_by = current_user.user_id
    entry.approval_notes = data.approval_notes
    entry.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(entry)
    sup = db.query(User).filter(User.user_id == entry.supervisor_id).first()
    return _entry_to_dict(entry, sup)


# ── GET /report — Admin / Ops Manager / CEO ───────────────────────────────────

@router.get("/report", summary="Travel allowance report grouped by supervisor")
def travel_allowance_report(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    badge_number: Optional[str] = Query(None, description="Filter by badge number"),
    status: Optional[str] = Query(None, description="Filter: pending|approved|rejected"),
    current_user: User = Depends(require_role(
        UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO, UserRole.OPERATIONS_MANAGER
    )),
    db: Session = Depends(get_db),
):
    target_sup_id = None
    if badge_number:
        sup = db.query(User).filter(User.badge_number == badge_number).first()
        if not sup:
            return {"supervisors": []}
        target_sup_id = sup.user_id

    q = db.query(TravelAllowanceEntry).filter(
        TravelAllowanceEntry.trip_date >= date_from,
        TravelAllowanceEntry.trip_date <= date_to,
        TravelAllowanceEntry.is_active == True,
    )
    if target_sup_id:
        q = q.filter(TravelAllowanceEntry.supervisor_id == target_sup_id)
    if status:
        q = q.filter(TravelAllowanceEntry.status == status)

    entries = q.order_by(TravelAllowanceEntry.trip_date.asc()).all()
    sup_ids = list({e.supervisor_id for e in entries})
    supervisors = db.query(User).filter(User.user_id.in_(sup_ids)).all() if sup_ids else []
    sup_map = {s.user_id: s for s in supervisors}

    grouped = defaultdict(list)
    for e in entries:
        grouped[e.supervisor_id].append(e)

    result = []
    for sid, trips in grouped.items():
        sup = sup_map.get(sid)
        if not sup:
            continue
        approved_total = round(sum(t.amount for t in trips if getattr(t, "status", "pending") == "approved"), 2)
        pending_total  = round(sum(t.amount for t in trips if getattr(t, "status", "pending") == "pending"), 2)
        result.append({
            "user_id": sid,
            "name": sup.name,
            "badge_number": sup.badge_number or "",
            "employee_code": sup.employee_code or "",
            "total_approved": approved_total,
            "total_pending": pending_total,
            "trips": [_entry_to_dict(t, sup) for t in trips],
        })

    return {"supervisors": result}


# ── POST / — Admin manual create ──────────────────────────────────────────────

@router.post("/", status_code=201, summary="Admin manually creates a travel allowance entry")
def create_entry(
    data: TravelAllowanceCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    trip_num = _next_trip_number(db, data.trip_date)
    entry = TravelAllowanceEntry(
        entry_id=str(uuid.uuid4()),
        supervisor_id=data.supervisor_id,
        trip_date=data.trip_date,
        trip_number=trip_num,
        from_site=data.from_site,
        to_site=data.to_site,
        amount=data.amount,
        notes=data.notes,
        status="approved",
        submitted_by=current_user.user_id,
        approved_by=current_user.user_id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    sup = db.query(User).filter(User.user_id == data.supervisor_id).first()
    return _entry_to_dict(entry, sup)


# ── PUT /{entry_id} ───────────────────────────────────────────────────────────

@router.put("/{entry_id}", summary="Update a travel allowance entry")
def update_entry(
    entry_id: str,
    data: TravelAllowanceUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO)),
    db: Session = Depends(get_db),
):
    entry = db.query(TravelAllowanceEntry).filter(
        TravelAllowanceEntry.entry_id == entry_id,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if data.amount is not None:
        entry.amount = data.amount
    if data.notes is not None:
        entry.notes = data.notes
    if data.from_site is not None:
        entry.from_site = data.from_site
    if data.to_site is not None:
        entry.to_site = data.to_site
    if data.status is not None:
        entry.status = data.status
    if data.approval_notes is not None:
        entry.approval_notes = data.approval_notes
    entry.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(entry)
    sup = db.query(User).filter(User.user_id == entry.supervisor_id).first()
    return _entry_to_dict(entry, sup)


# ── DELETE /{entry_id} ────────────────────────────────────────────────────────

@router.delete("/{entry_id}", summary="Delete a travel allowance entry")
def delete_entry(
    entry_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    entry = db.query(TravelAllowanceEntry).filter(
        TravelAllowanceEntry.entry_id == entry_id,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    entry.is_active = False
    entry.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "Entry deleted"}


# ── GET /export ───────────────────────────────────────────────────────────────

@router.get("/export", summary="Export travel allowance as CSV")
def export_csv(
    date_from: date = Query(...),
    date_to: date = Query(...),
    badge_number: Optional[str] = Query(None),
    current_user: User = Depends(require_role(
        UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO, UserRole.OPERATIONS_MANAGER
    )),
    db: Session = Depends(get_db),
):
    target_sup_id = None
    if badge_number:
        sup = db.query(User).filter(User.badge_number == badge_number).first()
        if sup:
            target_sup_id = sup.user_id

    q = db.query(TravelAllowanceEntry).filter(
        TravelAllowanceEntry.trip_date >= date_from,
        TravelAllowanceEntry.trip_date <= date_to,
        TravelAllowanceEntry.is_active == True,
    )
    if target_sup_id:
        q = q.filter(TravelAllowanceEntry.supervisor_id == target_sup_id)
    entries = q.order_by(TravelAllowanceEntry.trip_date.asc()).all()

    sup_ids = list({e.supervisor_id for e in entries})
    supervisors = db.query(User).filter(User.user_id.in_(sup_ids)).all() if sup_ids else []
    sup_map = {s.user_id: s for s in supervisors}

    headers = ["الاكواد", "مسلسل", "الاسم", "التاريخ", "المبلغ", "رقم الرحلة", "من", "الى", "الحالة", "ملاحظة"]
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(headers)

    for idx, entry in enumerate(entries, start=1):
        sup = sup_map.get(entry.supervisor_id)
        writer.writerow([
            sup.employee_code or sup.badge_number or "" if sup else "",
            idx,
            sup.name if sup else "Unknown",
            entry.trip_date.isoformat(),
            round(entry.amount, 2),
            entry.trip_number,
            entry.from_site,
            entry.to_site,
            getattr(entry, "status", "pending"),
            entry.notes or "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=travel_allowance_{date_from}_to_{date_to}.csv"},
    )
