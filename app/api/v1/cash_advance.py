"""
SecureTrack Platform — Cash Advance Routes
Leader creates cash advance requests for guards → Supervisor approves/rejects → Admin final approval.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.api.deps import require_role, get_current_user
from app.models.user import User
from app.models.site import Site
from app.models.cash_advance import CashAdvance
from app.models.guard_roster import GuardRoster
from app.models.shift import Shift
from app.enums import UserRole, CashAdvanceStatus

router = APIRouter()


# ── Schemas ──

class CashAdvanceCreate(BaseModel):
    guard_id: str
    site_id: str
    amount: float = Field(..., gt=0)
    installment_months: int = Field(..., ge=1, le=24)


class CashAdvanceReview(BaseModel):
    status: str  # "supervisor_approved" / "supervisor_rejected" for supervisor; "admin_approved" / "admin_rejected" / "admin_modified" for admin
    notes: Optional[str] = None
    approved_amount: Optional[float] = Field(None, gt=0)  # Only for admin_modified


# ── Leader Endpoints ──

@router.post("", status_code=201, summary="Leader creates a cash advance request")
def create_cash_advance(
    data: CashAdvanceCreate,
    current_user: User = Depends(require_role(UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    """Leader creates a cash advance request for a guard at their site."""
    # Validate guard exists
    guard = db.query(User).filter(User.user_id == data.guard_id).first()
    if not guard:
        raise HTTPException(status_code=404, detail="Guard not found")

    # Validate site exists
    site = db.query(Site).filter(Site.site_id == data.site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    amount_per_month = round(data.amount / data.installment_months, 2)

    advance = CashAdvance(
        advance_id=str(uuid.uuid4()),
        guard_id=data.guard_id,
        guard_name=guard.name,
        guard_code=guard.badge_number or "",
        leader_id=current_user.user_id,
        leader_name=current_user.name,
        site_id=data.site_id,
        site_name=site.name,
        amount=data.amount,
        installment_months=data.installment_months,
        amount_per_month=amount_per_month,
        status=CashAdvanceStatus.PENDING.value,
    )

    db.add(advance)
    db.commit()
    db.refresh(advance)

    return _advance_to_dict(advance)


@router.get("/leader/my-requests", summary="Leader views their submitted requests")
def leader_get_requests(
    status_filter: Optional[str] = Query(None, description="Filter by status"),
    current_user: User = Depends(require_role(UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    """Leader views all cash advance requests they created."""
    query = db.query(CashAdvance).filter(CashAdvance.leader_id == current_user.user_id)

    if status_filter:
        query = query.filter(CashAdvance.status == status_filter)

    advances = query.order_by(CashAdvance.created_at.desc()).all()

    return {
        "total": len(advances),
        "requests": [_advance_to_dict(a) for a in advances],
    }


@router.get("/leader/rejected", summary="Leader views rejected requests")
def leader_get_rejected(
    current_user: User = Depends(require_role(UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    """Leader views cash advance requests that were rejected by supervisor."""
    advances = (
        db.query(CashAdvance)
        .filter(CashAdvance.leader_id == current_user.user_id)
        .filter(CashAdvance.status == CashAdvanceStatus.SUPERVISOR_REJECTED.value)
        .order_by(CashAdvance.created_at.desc())
        .all()
    )

    return {
        "total": len(advances),
        "requests": [_advance_to_dict(a) for a in advances],
    }


# ── Supervisor Endpoints ──

@router.get("/supervisor/pending", summary="Supervisor views pending requests")
def supervisor_get_pending(
    current_user: User = Depends(require_role(UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Supervisor views cash advance requests awaiting their review."""
    advances = (
        db.query(CashAdvance)
        .filter(CashAdvance.status == CashAdvanceStatus.PENDING.value)
        .order_by(CashAdvance.created_at.desc())
        .all()
    )

    return {
        "total": len(advances),
        "requests": [_advance_to_dict(a) for a in advances],
    }


@router.put("/supervisor/{advance_id}", summary="Supervisor approves or rejects")
def supervisor_review(
    advance_id: str,
    review: CashAdvanceReview,
    current_user: User = Depends(require_role(UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Supervisor approves or rejects a cash advance request."""
    advance = db.query(CashAdvance).filter(CashAdvance.advance_id == advance_id).first()
    if not advance:
        raise HTTPException(status_code=404, detail="Cash advance request not found")

    if advance.status != CashAdvanceStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="Request is not pending supervisor review")

    if review.status not in [CashAdvanceStatus.SUPERVISOR_APPROVED.value, CashAdvanceStatus.SUPERVISOR_REJECTED.value]:
        raise HTTPException(status_code=400, detail="Invalid status. Use 'supervisor_approved' or 'supervisor_rejected'")

    advance.status = review.status
    advance.supervisor_id = current_user.user_id
    advance.supervisor_notes = review.notes
    advance.supervisor_reviewed_at = datetime.now(timezone.utc)
    advance.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(advance)

    return _advance_to_dict(advance)


# ── Admin Endpoints ──

@router.get("/admin/pending", summary="Admin views supervisor-approved requests")
def admin_get_pending(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Admin views cash advance requests approved by supervisor, awaiting admin decision."""
    advances = (
        db.query(CashAdvance)
        .filter(CashAdvance.status == CashAdvanceStatus.SUPERVISOR_APPROVED.value)
        .order_by(CashAdvance.created_at.desc())
        .all()
    )

    return {
        "total": len(advances),
        "requests": [_advance_to_dict(a) for a in advances],
    }


@router.get("/admin/all", summary="Admin views all cash advance requests")
def admin_get_all(
    status_filter: Optional[str] = Query(None, description="Filter by status"),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Admin views all cash advance requests with optional status filter."""
    query = db.query(CashAdvance)

    if status_filter:
        query = query.filter(CashAdvance.status == status_filter)

    advances = query.order_by(CashAdvance.created_at.desc()).all()

    return {
        "total": len(advances),
        "requests": [_advance_to_dict(a) for a in advances],
    }


@router.put("/admin/{advance_id}", summary="Admin approves, rejects, or modifies")
def admin_review(
    advance_id: str,
    review: CashAdvanceReview,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Admin approves, rejects, or modifies a cash advance request."""
    advance = db.query(CashAdvance).filter(CashAdvance.advance_id == advance_id).first()
    if not advance:
        raise HTTPException(status_code=404, detail="Cash advance request not found")

    if advance.status != CashAdvanceStatus.SUPERVISOR_APPROVED.value:
        raise HTTPException(status_code=400, detail="Request is not pending admin review")

    valid_statuses = [
        CashAdvanceStatus.ADMIN_APPROVED.value,
        CashAdvanceStatus.ADMIN_REJECTED.value,
        CashAdvanceStatus.ADMIN_MODIFIED.value,
    ]
    if review.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Use one of: {', '.join(valid_statuses)}")

    if review.status == CashAdvanceStatus.ADMIN_MODIFIED.value:
        if not review.approved_amount:
            raise HTTPException(status_code=400, detail="approved_amount is required when modifying")
        advance.approved_amount = review.approved_amount
        advance.amount_per_month = round(review.approved_amount / advance.installment_months, 2)

    advance.status = review.status
    advance.admin_id = current_user.user_id
    advance.admin_notes = review.notes
    advance.admin_reviewed_at = datetime.now(timezone.utc)
    advance.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(advance)

    return _advance_to_dict(advance)


# ── Helper ──

def _advance_to_dict(a: CashAdvance) -> dict:
    return {
        "advance_id": a.advance_id,
        "guard_id": a.guard_id,
        "guard_name": a.guard_name,
        "guard_code": a.guard_code,
        "leader_id": a.leader_id,
        "leader_name": a.leader_name,
        "site_id": a.site_id,
        "site_name": a.site_name,
        "amount": a.amount,
        "installment_months": a.installment_months,
        "amount_per_month": a.amount_per_month,
        "status": a.status,
        "approved_amount": a.approved_amount,
        "supervisor_id": a.supervisor_id,
        "supervisor_notes": a.supervisor_notes,
        "supervisor_reviewed_at": a.supervisor_reviewed_at.isoformat() if a.supervisor_reviewed_at else None,
        "admin_id": a.admin_id,
        "admin_notes": a.admin_notes,
        "admin_reviewed_at": a.admin_reviewed_at.isoformat() if a.admin_reviewed_at else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }
