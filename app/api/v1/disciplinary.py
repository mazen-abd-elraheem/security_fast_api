"""
SecureTrack — Disciplinary Actions API
HR manages warnings, deductions, and suspensions for guards.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, Query, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import require_role, get_current_user
from app.models.user import User
from app.models.disciplinary_action import DisciplinaryAction
from app.enums import UserRole

router = APIRouter()


# ── Schemas ──

class DisciplinaryCreate(BaseModel):
    guard_id: str
    action_type: str = Field(..., description="warning | deduction | suspension")
    severity: str = Field("moderate", description="minor | moderate | major | critical")
    reason: str
    reference_number: Optional[str] = None
    deduction_amount: Optional[float] = None
    deduction_days: Optional[int] = None
    suspension_start: Optional[str] = None
    suspension_end: Optional[str] = None

class DisciplinaryUpdate(BaseModel):
    status: Optional[str] = None
    reason: Optional[str] = None
    deduction_amount: Optional[float] = None
    reference_number: Optional[str] = None


def _action_to_dict(a: DisciplinaryAction) -> dict:
    return {
        "action_id": a.action_id,
        "guard_id": a.guard_id,
        "guard_name": a.guard_name,
        "guard_code": a.guard_code,
        "action_type": a.action_type,
        "severity": a.severity,
        "reason": a.reason,
        "reference_number": a.reference_number,
        "deduction_amount": a.deduction_amount,
        "deduction_days": a.deduction_days,
        "suspension_start": a.suspension_start.isoformat() if a.suspension_start else None,
        "suspension_end": a.suspension_end.isoformat() if a.suspension_end else None,
        "status": a.status,
        "issued_by": a.issued_by,
        "issued_by_name": a.issued_by_name,
        "linked_to_payroll": a.linked_to_payroll,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


# ── Endpoints ──

@router.post("/", status_code=status.HTTP_201_CREATED, summary="Issue a disciplinary action")
def create_disciplinary_action(
    body: DisciplinaryCreate,
    current_user: User = Depends(require_role(UserRole.HR)),
    db: Session = Depends(get_db),
):
    """HR issues a warning, deduction, or suspension to a guard."""
    guard = db.query(User).filter(User.user_id == body.guard_id).first()
    if not guard:
        raise HTTPException(status_code=404, detail="Guard not found")

    action = DisciplinaryAction(
        action_id=str(uuid.uuid4()),
        guard_id=body.guard_id,
        guard_name=guard.name,
        guard_code=getattr(guard, 'employee_code', None) or getattr(guard, 'badge_number', None),
        action_type=body.action_type,
        severity=body.severity,
        reason=body.reason,
        reference_number=body.reference_number,
        deduction_amount=body.deduction_amount,
        deduction_days=body.deduction_days,
        suspension_start=datetime.fromisoformat(body.suspension_start) if body.suspension_start else None,
        suspension_end=datetime.fromisoformat(body.suspension_end) if body.suspension_end else None,
        issued_by=current_user.user_id,
        issued_by_name=current_user.name,
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return _action_to_dict(action)


@router.get("/", summary="List all disciplinary actions")
def list_disciplinary_actions(
    action_type: Optional[str] = Query(None),
    guard_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_role(UserRole.HR)),
    db: Session = Depends(get_db),
):
    query = db.query(DisciplinaryAction)
    if action_type:
        query = query.filter(DisciplinaryAction.action_type == action_type)
    if guard_id:
        query = query.filter(DisciplinaryAction.guard_id == guard_id)
    if status_filter:
        query = query.filter(DisciplinaryAction.status == status_filter)

    total = query.count()
    actions = query.order_by(DisciplinaryAction.created_at.desc()).offset(skip).limit(limit).all()

    return {
        "total": total,
        "actions": [_action_to_dict(a) for a in actions],
    }


@router.get("/{action_id}", summary="Get a single disciplinary action")
def get_disciplinary_action(
    action_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    action = db.query(DisciplinaryAction).filter(DisciplinaryAction.action_id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    return _action_to_dict(action)


@router.patch("/{action_id}", summary="Update a disciplinary action")
def update_disciplinary_action(
    action_id: str,
    body: DisciplinaryUpdate,
    current_user: User = Depends(require_role(UserRole.HR)),
    db: Session = Depends(get_db),
):
    action = db.query(DisciplinaryAction).filter(DisciplinaryAction.action_id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")

    if body.status is not None:
        action.status = body.status
    if body.reason is not None:
        action.reason = body.reason
    if body.deduction_amount is not None:
        action.deduction_amount = body.deduction_amount
    if body.reference_number is not None:
        action.reference_number = body.reference_number

    db.commit()
    db.refresh(action)
    return _action_to_dict(action)
