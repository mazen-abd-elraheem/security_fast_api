"""
SecureTrack — Separations API (Resignation / Termination / Exclusion)
Flow: Leader → Supervisor (must confirm uniform return) → Ops Manager → HR (final + settlement).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import uuid

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.separation_request import SeparationRequest
from app.models.user import User
from app.enums import UserRole

router = APIRouter()


# ── Schemas ──
class SeparationCreate(BaseModel):
    user_id: str
    user_name: str
    employee_code: Optional[str] = None
    site_id: str
    site_name: str
    separation_type: str  # resignation / termination / exclusion
    reason: str


class SeparationAction(BaseModel):
    action: str  # approve / reject
    notes: Optional[str] = None
    uniform_returned: Optional[bool] = None  # Only Supervisor sets this
    financial_settlement: Optional[float] = None  # Only HR sets this


class SeparationResponse(BaseModel):
    separation_id: str
    user_id: str
    user_name: str
    employee_code: Optional[str]
    site_id: str
    site_name: str
    separation_type: str
    reason: str
    status: str
    initiated_by: Optional[str]
    initiated_by_name: Optional[str]
    supervisor_id: Optional[str]
    supervisor_notes: Optional[str]
    supervisor_reviewed_at: Optional[datetime]
    uniform_returned: bool
    uniform_return_confirmed_by: Optional[str]
    ops_manager_id: Optional[str]
    ops_manager_notes: Optional[str]
    ops_manager_reviewed_at: Optional[datetime]
    hr_id: Optional[str]
    hr_notes: Optional[str]
    hr_reviewed_at: Optional[datetime]
    financial_settlement: Optional[float]
    assets_returned: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ── Endpoints ──

@router.post("/", response_model=SeparationResponse, status_code=status.HTTP_201_CREATED)
def create_separation(
    payload: SeparationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Leader initiates a separation request."""
    if current_user.role not in ("leader", "admin"):
        raise HTTPException(status_code=403, detail="Only Leaders can initiate separation requests")

    separation = SeparationRequest(
        separation_id=str(uuid.uuid4()),
        user_id=payload.user_id,
        user_name=payload.user_name,
        employee_code=payload.employee_code,
        site_id=payload.site_id,
        site_name=payload.site_name,
        separation_type=payload.separation_type,
        reason=payload.reason,
        status="pending_supervisor",
        initiated_by=current_user.user_id,
        initiated_by_name=current_user.name,
    )
    db.add(separation)
    db.commit()
    db.refresh(separation)
    return separation


@router.get("/", response_model=List[SeparationResponse])
def list_separations(
    site_id: Optional[str] = None,
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List separation requests filtered by role."""
    query = db.query(SeparationRequest)

    if current_user.role == "leader":
        query = query.filter(SeparationRequest.initiated_by == current_user.user_id)
    elif current_user.role == "supervisor":
        query = query.filter(SeparationRequest.status == "pending_supervisor")
    elif current_user.role == "operations_manager":
        query = query.filter(SeparationRequest.status == "pending_ops_mgr")
    elif current_user.role == "hr":
        query = query.filter(SeparationRequest.status == "pending_hr")

    if site_id:
        query = query.filter(SeparationRequest.site_id == site_id)
    if status_filter:
        query = query.filter(SeparationRequest.status == status_filter)

    return query.order_by(SeparationRequest.created_at.desc()).all()


@router.get("/{separation_id}", response_model=SeparationResponse)
def get_separation(
    separation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sep = db.query(SeparationRequest).filter(SeparationRequest.separation_id == separation_id).first()
    if not sep:
        raise HTTPException(status_code=404, detail="Separation request not found")
    return sep


@router.patch("/{separation_id}/action", response_model=SeparationResponse)
def action_separation(
    separation_id: str,
    payload: SeparationAction,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Process a separation request through the approval chain."""
    sep = db.query(SeparationRequest).filter(SeparationRequest.separation_id == separation_id).first()
    if not sep:
        raise HTTPException(status_code=404, detail="Separation request not found")

    now = datetime.now(timezone.utc)

    if payload.action == "reject":
        sep.status = "rejected"
        if current_user.role == "supervisor":
            sep.supervisor_id = current_user.user_id
            sep.supervisor_notes = payload.notes
            sep.supervisor_reviewed_at = now
        elif current_user.role == "operations_manager":
            sep.ops_manager_id = current_user.user_id
            sep.ops_manager_notes = payload.notes
            sep.ops_manager_reviewed_at = now
        elif current_user.role == "hr":
            sep.hr_id = current_user.user_id
            sep.hr_notes = payload.notes
            sep.hr_reviewed_at = now

    elif payload.action == "approve":
        # Supervisor step — MUST confirm uniform return
        if current_user.role == "supervisor" and sep.status == "pending_supervisor":
            if not payload.uniform_returned:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot approve: guard must return all uniforms and gear first"
                )
            sep.uniform_returned = True
            sep.uniform_return_confirmed_by = current_user.user_id
            sep.supervisor_id = current_user.user_id
            sep.supervisor_notes = payload.notes
            sep.supervisor_reviewed_at = now
            sep.status = "pending_ops_mgr"

        # Ops Manager step
        elif current_user.role == "operations_manager" and sep.status == "pending_ops_mgr":
            sep.ops_manager_id = current_user.user_id
            sep.ops_manager_notes = payload.notes
            sep.ops_manager_reviewed_at = now
            sep.status = "pending_hr"

        # HR final step — includes financial settlement
        elif current_user.role == "hr" and sep.status == "pending_hr":
            sep.hr_id = current_user.user_id
            sep.hr_notes = payload.notes
            sep.hr_reviewed_at = now
            sep.assets_returned = True
            if payload.financial_settlement is not None:
                sep.financial_settlement = payload.financial_settlement
            sep.status = "completed"

            # Deactivate the user account
            user = db.query(User).filter(User.user_id == sep.user_id).first()
            if user:
                user.is_active = False
                user.status = "separated"
                # SECURITY: Instantly revoke all active tokens
                from app.core.security import revoke_all_user_tokens
from app.core.audit import log_audit, log_create, log_update, log_delete, log_read, snapshot
                revoke_all_user_tokens(user.user_id, "separation", db)

        elif current_user.role == "admin":
            sep.hr_id = current_user.user_id
            sep.hr_notes = payload.notes
            sep.hr_reviewed_at = now
            sep.status = "completed"
        else:
            raise HTTPException(status_code=400, detail="Cannot approve at this stage with your role")

    db.commit()
    db.refresh(sep)
    return sep
