"""
SecureTrack Platform — Deduction Rules Routes
Admin-configurable salary deduction rules: CRUD + defaults seeding.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import uuid

from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User
from app.models.deduction_rule import DeductionRule
from app.enums import UserRole

router = APIRouter()

# ── Default rules (seeded on first fetch if table is empty) ──

DEFAULT_RULES = [
    {
        "rule_type": "absent",
        "label": "Absent – Full Day",
        "description": "Full deduction when guard does not show up for shift",
        "amount": 200.0,
        "is_per_minute": False,
        "threshold_minutes": 0,
        "is_active": True,
        "is_bonus": False,
    },
    {
        "rule_type": "late",
        "label": "Late Arrival",
        "description": "Per-minute deduction after grace period",
        "amount": 2.0,
        "is_per_minute": True,
        "threshold_minutes": 15,
        "is_active": True,
        "is_bonus": False,
    },
    {
        "rule_type": "early_leave",
        "label": "Early Leave",
        "description": "Per-minute deduction for leaving before shift end",
        "amount": 2.0,
        "is_per_minute": True,
        "threshold_minutes": 10,
        "is_active": True,
        "is_bonus": False,
    },
    {
        "rule_type": "no_checkout",
        "label": "No Checkout",
        "description": "Fixed penalty when guard doesn't check out at shift end",
        "amount": 50.0,
        "is_per_minute": False,
        "threshold_minutes": 0,
        "is_active": True,
        "is_bonus": False,
    },
    {
        "rule_type": "overtime_bonus",
        "label": "Overtime Bonus",
        "description": "Per-minute bonus for working beyond scheduled shift",
        "amount": 3.0,
        "is_per_minute": True,
        "threshold_minutes": 30,
        "is_active": False,
        "is_bonus": True,
    },
    {
        "rule_type": "short_shift",
        "label": "Short Shift",
        "description": "Deduction when actual hours are less than 50% of scheduled",
        "amount": 150.0,
        "is_per_minute": False,
        "threshold_minutes": 0,
        "is_active": True,
        "is_bonus": False,
    },
]


def _seed_defaults(db: Session):
    """Seed default rules if table is empty."""
    count = db.query(DeductionRule).count()
    if count > 0:
        return
    for rule_data in DEFAULT_RULES:
        rule = DeductionRule(
            rule_id=str(uuid.uuid4()),
            **rule_data,
        )
        db.add(rule)
    db.commit()


# ── Schemas ──

class RuleUpdate(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    amount: Optional[float] = None
    is_per_minute: Optional[bool] = None
    threshold_minutes: Optional[int] = None
    is_active: Optional[bool] = None
    is_bonus: Optional[bool] = None


class RuleCreate(BaseModel):
    rule_type: str
    label: str
    description: Optional[str] = None
    amount: float = 0.0
    is_per_minute: bool = False
    threshold_minutes: int = 0
    is_active: bool = True
    is_bonus: bool = False


# ── Endpoints ──

@router.get("/rules", summary="List all deduction rules")
def list_rules(
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    """List all deduction rules. Seeds defaults if none exist."""
    _seed_defaults(db)
    rules = db.query(DeductionRule).order_by(DeductionRule.rule_type).all()
    return {
        "rules": [
            {
                "rule_id": r.rule_id,
                "rule_type": r.rule_type,
                "label": r.label,
                "description": r.description,
                "amount": r.amount,
                "is_per_minute": r.is_per_minute,
                "threshold_minutes": r.threshold_minutes,
                "is_active": r.is_active,
                "is_bonus": r.is_bonus,
                "currency": "EGP",
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rules
        ]
    }


@router.put("/rules/{rule_id}", summary="Update a deduction rule")
def update_rule(
    rule_id: str,
    update: RuleUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    """Update a specific deduction rule's config."""
    rule = db.query(DeductionRule).filter(DeductionRule.rule_id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    update_data = update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(rule, field, value)

    db.commit()
    db.refresh(rule)

    return {
        "message": f"Rule '{rule.label}' updated",
        "rule": {
            "rule_id": rule.rule_id,
            "rule_type": rule.rule_type,
            "label": rule.label,
            "description": rule.description,
            "amount": rule.amount,
            "is_per_minute": rule.is_per_minute,
            "threshold_minutes": rule.threshold_minutes,
            "is_active": rule.is_active,
            "is_bonus": rule.is_bonus,
            "currency": "EGP",
        },
    }


@router.post("/rules", summary="Create a custom deduction rule")
def create_rule(
    rule: RuleCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Create a new custom deduction rule."""
    existing = db.query(DeductionRule).filter(DeductionRule.rule_type == rule.rule_type).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Rule type '{rule.rule_type}' already exists")

    new_rule = DeductionRule(
        rule_id=str(uuid.uuid4()),
        **rule.model_dump(),
    )
    db.add(new_rule)
    db.commit()
    db.refresh(new_rule)

    return {
        "message": f"Rule '{new_rule.label}' created",
        "rule": {
            "rule_id": new_rule.rule_id,
            "rule_type": new_rule.rule_type,
            "label": new_rule.label,
            "amount": new_rule.amount,
            "is_active": new_rule.is_active,
        },
    }


@router.delete("/rules/{rule_id}", summary="Delete a deduction rule")
def delete_rule(
    rule_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Delete a deduction rule."""
    rule = db.query(DeductionRule).filter(DeductionRule.rule_id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    db.delete(rule)
    db.commit()
    return {"message": f"Rule '{rule.label}' deleted"}


@router.post("/rules/reset-defaults", summary="Reset rules to defaults")
def reset_defaults(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Delete all rules and re-seed defaults."""
    db.query(DeductionRule).delete()
    db.commit()
    _seed_defaults(db)
    return {"message": "Rules reset to defaults"}
