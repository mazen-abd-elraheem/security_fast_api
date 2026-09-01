"""
SecureTrack Platform — Cash Advance Routes
Leader/Supervisor creates cash advance requests for guards.
Approval chain: Leader/Supervisor → Ops Manager → Admin → CEO
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.api.deps import require_role, get_current_user
from app.models.user import User
from app.models.site import Site
from app.models.cash_advance import CashAdvance
from app.enums import UserRole, CashAdvanceStatus

router = APIRouter()


# ── Schemas ──

class CashAdvanceCreate(BaseModel):
    guard_id: str
    site_id: str
    amount: float = Field(..., gt=0)
    installment_months: int = Field(..., ge=1, le=24)


class CashAdvanceReview(BaseModel):
    status: str
    notes: Optional[str] = None
    approved_amount: Optional[float] = Field(None, gt=0)


# ── Leader / Supervisor: Create ──

@router.post("", status_code=201, summary="Leader or Supervisor creates a cash advance request")
def create_cash_advance(
    data: CashAdvanceCreate,
    current_user: User = Depends(require_role(UserRole.LEADER, UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Leader or Supervisor creates a cash advance request for a guard."""
    guard = db.query(User).filter(User.user_id == data.guard_id).first()
    if not guard:
        raise HTTPException(status_code=404, detail="Guard not found")

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


@router.get("/leader/my-requests", summary="Leader/Supervisor views their submitted requests")
def leader_get_requests(
    status_filter: Optional[str] = Query(None, description="Filter by status"),
    current_user: User = Depends(require_role(UserRole.LEADER, UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Leader/Supervisor views all cash advance requests they created."""
    query = db.query(CashAdvance).filter(CashAdvance.leader_id == current_user.user_id)

    if status_filter:
        query = query.filter(CashAdvance.status == status_filter)

    advances = query.order_by(CashAdvance.created_at.desc()).all()

    return {
        "total": len(advances),
        "requests": [_advance_to_dict(a) for a in advances],
    }


# ── Ops Manager Endpoints ──

@router.get("/ops-manager/pending", summary="Ops Manager views pending requests")
def ops_manager_get_pending(
    current_user: User = Depends(require_role(UserRole.OPERATIONS_MANAGER)),
    db: Session = Depends(get_db),
):
    """Ops Manager views cash advance requests awaiting their review."""
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


@router.get("/ops-manager/history", summary="Ops Manager views advance history per employee")
def ops_manager_advance_history(
    employee_id: Optional[str] = Query(None),
    current_user: User = Depends(require_role(UserRole.OPERATIONS_MANAGER)),
    db: Session = Depends(get_db),
):
    """Ops Manager views advance history. Shows total count and amount per employee."""
    query = db.query(CashAdvance)
    if employee_id:
        query = query.filter(CashAdvance.guard_id == employee_id)

    advances = query.order_by(CashAdvance.created_at.desc()).all()

    # Group by employee
    employee_stats = {}
    for a in advances:
        if a.guard_id not in employee_stats:
            employee_stats[a.guard_id] = {
                "guard_id": a.guard_id,
                "guard_name": a.guard_name,
                "total_count": 0,
                "total_amount": 0,
            }
        employee_stats[a.guard_id]["total_count"] += 1
        employee_stats[a.guard_id]["total_amount"] += a.amount

    return {
        "total": len(advances),
        "requests": [_advance_to_dict(a) for a in advances],
        "employee_stats": list(employee_stats.values()),
    }


@router.put("/ops-manager/{advance_id}", summary="Ops Manager approves or rejects")
def ops_manager_review(
    advance_id: str,
    review: CashAdvanceReview,
    current_user: User = Depends(require_role(UserRole.OPERATIONS_MANAGER)),
    db: Session = Depends(get_db),
):
    """Ops Manager approves or rejects a cash advance request."""
    advance = db.query(CashAdvance).filter(CashAdvance.advance_id == advance_id).first()
    if not advance:
        raise HTTPException(status_code=404, detail="Cash advance request not found")

    if advance.status != CashAdvanceStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="Request is not pending ops manager review")

    valid = [CashAdvanceStatus.OPS_APPROVED.value, CashAdvanceStatus.OPS_REJECTED.value]
    if review.status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status. Use: {', '.join(valid)}")

    # Allow ops manager to modify amount
    if review.approved_amount:
        advance.approved_amount = review.approved_amount
        advance.amount_per_month = round(review.approved_amount / advance.installment_months, 2)

    advance.status = review.status
    advance.ops_manager_id = current_user.user_id
    advance.ops_manager_notes = review.notes
    advance.ops_reviewed_at = datetime.now(timezone.utc)
    advance.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(advance)

    return _advance_to_dict(advance)


# ── Admin Endpoints ──

@router.get("/admin/pending", summary="Admin views ops-approved requests")
def admin_get_pending(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Admin views cash advance requests approved by ops manager."""
    advances = (
        db.query(CashAdvance)
        .filter(CashAdvance.status == CashAdvanceStatus.OPS_APPROVED.value)
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

    if advance.status != CashAdvanceStatus.OPS_APPROVED.value:
        raise HTTPException(status_code=400, detail="Request is not pending admin review")

    valid = [
        CashAdvanceStatus.ADMIN_APPROVED.value,
        CashAdvanceStatus.ADMIN_REJECTED.value,
        CashAdvanceStatus.ADMIN_MODIFIED.value,
    ]
    if review.status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status. Use: {', '.join(valid)}")

    if review.status == CashAdvanceStatus.ADMIN_MODIFIED.value:
        if not review.approved_amount:
            raise HTTPException(status_code=400, detail="approved_amount required when modifying")
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


# ── CEO Endpoints ──

@router.get("/ceo/pending", summary="CEO views admin-approved requests")
def ceo_get_pending(
    current_user: User = Depends(require_role(UserRole.CEO)),
    db: Session = Depends(get_db),
):
    """CEO views cash advance requests approved by admin, awaiting final approval."""
    advances = (
        db.query(CashAdvance)
        .filter(CashAdvance.status.in_([
            CashAdvanceStatus.ADMIN_APPROVED.value,
            CashAdvanceStatus.ADMIN_MODIFIED.value,
        ]))
        .order_by(CashAdvance.created_at.desc())
        .all()
    )

    return {
        "total": len(advances),
        "requests": [_advance_to_dict(a) for a in advances],
    }


@router.put("/ceo/{advance_id}", summary="CEO final approval or rejection")
def ceo_review(
    advance_id: str,
    review: CashAdvanceReview,
    current_user: User = Depends(require_role(UserRole.CEO)),
    db: Session = Depends(get_db),
):
    """CEO gives final approval or rejection."""
    advance = db.query(CashAdvance).filter(CashAdvance.advance_id == advance_id).first()
    if not advance:
        raise HTTPException(status_code=404, detail="Cash advance request not found")

    if advance.status not in [CashAdvanceStatus.ADMIN_APPROVED.value, CashAdvanceStatus.ADMIN_MODIFIED.value]:
        raise HTTPException(status_code=400, detail="Request is not pending CEO review")

    valid = [CashAdvanceStatus.CEO_APPROVED.value, CashAdvanceStatus.CEO_REJECTED.value]
    if review.status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status. Use: {', '.join(valid)}")

    advance.status = review.status
    advance.ceo_id = current_user.user_id
    advance.ceo_notes = review.notes
    advance.ceo_reviewed_at = datetime.now(timezone.utc)
    advance.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(advance)

    return _advance_to_dict(advance)


# ── Guard Self-Service ──

@router.get("/my", summary="Guard views their own cash advances")
def guard_get_my_advances(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Guard/outdoor views all cash advances associated with them."""
    advances = db.query(CashAdvance).filter(
        CashAdvance.guard_id == current_user.user_id
    ).order_by(CashAdvance.created_at.desc()).all()
    return [_advance_to_dict(a) for a in advances]


# ── Accountant: View CEO-approved only ──

@router.get("/accountant/approved", summary="Accountant views CEO-approved advances for payroll")
def accountant_get_approved(
    current_user: User = Depends(require_role(UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    """Accountant views only CEO-approved advances for payroll processing."""
    advances = (
        db.query(CashAdvance)
        .filter(CashAdvance.status == CashAdvanceStatus.CEO_APPROVED.value)
        .order_by(CashAdvance.created_at.desc())
        .all()
    )

    return {
        "total": len(advances),
        "requests": [_advance_to_dict(a) for a in advances],
    }


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
        # Ops Manager
        "ops_manager_id": getattr(a, "ops_manager_id", None),
        "ops_manager_notes": getattr(a, "ops_manager_notes", None),
        "ops_reviewed_at": a.ops_reviewed_at.isoformat() if getattr(a, "ops_reviewed_at", None) else None,
        # Supervisor (legacy)
        "supervisor_id": a.supervisor_id,
        "supervisor_notes": a.supervisor_notes,
        "supervisor_reviewed_at": a.supervisor_reviewed_at.isoformat() if a.supervisor_reviewed_at else None,
        # Admin
        "admin_id": a.admin_id,
        "admin_notes": a.admin_notes,
        "admin_reviewed_at": a.admin_reviewed_at.isoformat() if a.admin_reviewed_at else None,
        # CEO
        "ceo_id": getattr(a, "ceo_id", None),
        "ceo_notes": getattr(a, "ceo_notes", None),
        "ceo_reviewed_at": a.ceo_reviewed_at.isoformat() if getattr(a, "ceo_reviewed_at", None) else None,
        # Timestamps
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }
