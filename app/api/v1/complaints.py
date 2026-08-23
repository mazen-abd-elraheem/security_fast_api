"""
SecureTrack — Complaints API
Leader submits on behalf of guard (primary) or Guard submits directly.
Flow: Submit → Leader reviews → Resolve or Escalate to HR.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import uuid

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.complaint import Complaint
from app.models.user import User

router = APIRouter()


# ── Schemas ──
class ComplaintCreate(BaseModel):
    guard_id: str
    guard_name: str
    site_id: str
    site_name: str
    subject: str
    description: Optional[str] = None
    category: Optional[str] = None


class ComplaintUpdate(BaseModel):
    status: Optional[str] = None  # in_review, resolved, escalated_to_hr, hr_resolved
    resolution_notes: Optional[str] = None


class ComplaintResponse(BaseModel):
    complaint_id: str
    guard_id: str
    guard_name: str
    submitted_by: str
    submitted_by_name: str
    submitted_by_role: str
    site_id: str
    site_name: str
    subject: str
    description: Optional[str]
    category: Optional[str]
    status: str
    resolution_notes: Optional[str]
    resolved_by: Optional[str]
    resolved_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ── Endpoints ──

@router.post("/", response_model=ComplaintResponse, status_code=status.HTTP_201_CREATED)
def create_complaint(
    payload: ComplaintCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a complaint. Leaders submit on behalf of guards, or guards submit directly."""
    allowed_roles = ["leader", "guard", "outdoor", "admin"]
    if current_user.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Not authorized to submit complaints")

    complaint = Complaint(
        complaint_id=str(uuid.uuid4()),
        guard_id=payload.guard_id,
        guard_name=payload.guard_name,
        submitted_by=current_user.user_id,
        submitted_by_name=current_user.name,
        submitted_by_role=current_user.role,
        site_id=payload.site_id,
        site_name=payload.site_name,
        subject=payload.subject,
        description=payload.description,
        category=payload.category,
        status="pending",
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    return complaint


@router.get("/", response_model=List[ComplaintResponse])
def list_complaints(
    site_id: Optional[str] = None,
    status: Optional[str] = None,
    guard_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List complaints. Filtered by role visibility."""
    query = db.query(Complaint)

    # Role-based filtering
    if current_user.role == "leader":
        query = query.filter(Complaint.site_id == site_id) if site_id else query
    elif current_user.role in ("guard", "outdoor"):
        query = query.filter(Complaint.guard_id == current_user.user_id)
    # admin, hr, ops_manager see all

    if status:
        query = query.filter(Complaint.status == status)
    if guard_id:
        query = query.filter(Complaint.guard_id == guard_id)

    return query.order_by(Complaint.created_at.desc()).all()


@router.get("/{complaint_id}", response_model=ComplaintResponse)
def get_complaint(
    complaint_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single complaint by ID."""
    complaint = db.query(Complaint).filter(Complaint.complaint_id == complaint_id).first()
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")
    return complaint


@router.patch("/{complaint_id}", response_model=ComplaintResponse)
def update_complaint(
    complaint_id: str,
    payload: ComplaintUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update complaint status (resolve, escalate). Leaders, HR, Admin."""
    allowed_roles = ["leader", "hr", "admin", "operations_manager"]
    if current_user.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Not authorized to update complaints")

    complaint = db.query(Complaint).filter(Complaint.complaint_id == complaint_id).first()
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    if payload.status:
        complaint.status = payload.status
        if payload.status in ("resolved", "hr_resolved"):
            complaint.resolved_by = current_user.user_id
            complaint.resolved_at = datetime.now(timezone.utc)

    if payload.resolution_notes:
        complaint.resolution_notes = payload.resolution_notes

    db.commit()
    db.refresh(complaint)
    return complaint
