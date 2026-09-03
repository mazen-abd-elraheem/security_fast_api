"""
SecureTrack — Daily Logbook API (دفتر الأحوال)
Leader writes daily → Supervisor reviews aggregated → Ops Manager views all.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime, timezone
import uuid

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.daily_logbook import DailyLogbook
from app.models.user import User
from app.core.audit import log_audit, log_create, log_update, log_delete, log_read, snapshot

router = APIRouter()


# ── Schemas ──
class LogbookCreate(BaseModel):
    site_id: str
    site_name: str
    date: date
    shift_label: Optional[str] = None
    events_summary: str
    incidents_count: int = 0
    guards_present: Optional[int] = None
    guards_absent: Optional[int] = None
    notes: Optional[str] = None
    attachments: Optional[str] = None  # JSON string of URLs


class LogbookReview(BaseModel):
    supervisor_notes: Optional[str] = None


class LogbookResponse(BaseModel):
    logbook_id: str
    site_id: str
    site_name: str
    leader_id: str
    leader_name: str
    date: date
    shift_label: Optional[str]
    events_summary: str
    incidents_count: int
    guards_present: Optional[int]
    guards_absent: Optional[int]
    notes: Optional[str]
    attachments: Optional[str]
    reviewed_by_supervisor: Optional[str]
    supervisor_notes: Optional[str]
    supervisor_reviewed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ── Endpoints ──

@router.post("/", response_model=LogbookResponse, status_code=status.HTTP_201_CREATED)
def create_logbook_entry(
    payload: LogbookCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a daily logbook entry. Only Leaders can write."""
    if current_user.role not in ("leader", "admin"):
        raise HTTPException(status_code=403, detail="Only Leaders can write logbook entries")

    entry = DailyLogbook(
        logbook_id=str(uuid.uuid4()),
        site_id=payload.site_id,
        site_name=payload.site_name,
        leader_id=current_user.user_id,
        leader_name=current_user.name,
        date=payload.date,
        shift_label=payload.shift_label,
        events_summary=payload.events_summary,
        incidents_count=payload.incidents_count,
        guards_present=payload.guards_present,
        guards_absent=payload.guards_absent,
        notes=payload.notes,
        attachments=payload.attachments,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.get("/", response_model=List[LogbookResponse])
def list_logbook_entries(
    site_id: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List logbook entries. Leaders see their site, Supervisors/Ops see all."""
    query = db.query(DailyLogbook)

    if current_user.role == "leader":
        query = query.filter(DailyLogbook.leader_id == current_user.user_id)
    # supervisor, ops_manager, admin, hr see all

    if site_id:
        query = query.filter(DailyLogbook.site_id == site_id)
    if date_from:
        query = query.filter(DailyLogbook.date >= date_from)
    if date_to:
        query = query.filter(DailyLogbook.date <= date_to)

    return query.order_by(DailyLogbook.date.desc()).all()


@router.get("/{logbook_id}", response_model=LogbookResponse)
def get_logbook_entry(
    logbook_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    entry = db.query(DailyLogbook).filter(DailyLogbook.logbook_id == logbook_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Logbook entry not found")
    return entry


@router.patch("/{logbook_id}/review", response_model=LogbookResponse)
def review_logbook_entry(
    logbook_id: str,
    payload: LogbookReview,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Supervisor reviews a logbook entry."""
    if current_user.role not in ("supervisor", "operations_manager", "admin"):
        raise HTTPException(status_code=403, detail="Only Supervisors can review logbook entries")

    entry = db.query(DailyLogbook).filter(DailyLogbook.logbook_id == logbook_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Logbook entry not found")

    entry.reviewed_by_supervisor = current_user.user_id
    entry.supervisor_notes = payload.supervisor_notes
    entry.supervisor_reviewed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(entry)
    return entry
