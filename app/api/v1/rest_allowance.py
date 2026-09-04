"""
SecureTrack — Rest Allowance Config API
Admin/Accountant can configure rest allowance rates per role.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User
from app.models.rest_allowance_config import RestAllowanceConfig
from app.models.employee_rest_allowance import EmployeeRestAllowance
from app.enums import UserRole

router = APIRouter()


# ── Schemas ──

class RestAllowanceConfigCreate(BaseModel):
    role: str
    value: float = Field(..., ge=0)
    is_days_multiplier: bool = False


class RestAllowanceConfigUpdate(BaseModel):
    value: Optional[float] = Field(None, ge=0)
    is_days_multiplier: Optional[bool] = None
    is_active: Optional[bool] = None


class EmployeeRestAllowanceCreate(BaseModel):
    badge_number: str
    value: float = Field(..., ge=0)
    is_days_multiplier: bool = True
    month_year: Optional[str] = None  # e.g., "2026-09". If None, applies globally/permanently


# ── Endpoints ──

@router.get("/config", summary="List all rest allowance configs")
def list_configs(
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    """List rest allowance rate per role."""
    configs = db.query(RestAllowanceConfig).order_by(RestAllowanceConfig.role).all()
    return {
        "configs": [
            {
                "config_id": c.config_id,
                "role": c.role,
                "value": c.value,
                "is_days_multiplier": c.is_days_multiplier,
                "is_active": c.is_active,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
            for c in configs
        ]
    }


@router.post("/config", status_code=201, summary="Create rest allowance config for a role")
def create_config(
    data: RestAllowanceConfigCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    """Create a new rest allowance config for a role."""
    existing = db.query(RestAllowanceConfig).filter(
        RestAllowanceConfig.role == data.role
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Config for role '{data.role}' already exists")

    config = RestAllowanceConfig(
        config_id=str(uuid.uuid4()),
        role=data.role,
        value=data.value,
        is_days_multiplier=data.is_days_multiplier,
    )
    db.add(config)
    db.commit()
    db.refresh(config)

    return {
        "config_id": config.config_id,
        "role": config.role,
        "value": config.value,
        "is_days_multiplier": config.is_days_multiplier,
        "is_active": config.is_active,
    }


@router.put("/config/{role}", summary="Update rest allowance config for a role")
def update_config(
    role: str,
    data: RestAllowanceConfigUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    """Update the rest allowance rate/days for a specific role."""
    config = db.query(RestAllowanceConfig).filter(
        RestAllowanceConfig.role == role
    ).first()

    if not config:
        # Auto-create if not found
        config = RestAllowanceConfig(
            config_id=str(uuid.uuid4()),
            role=role,
            value=data.value if data.value is not None else 0.0,
            is_days_multiplier=data.is_days_multiplier if data.is_days_multiplier is not None else False,
            is_active=data.is_active if data.is_active is not None else True,
        )
        db.add(config)
    else:
        if data.value is not None:
            config.value = data.value
        if data.is_days_multiplier is not None:
            config.is_days_multiplier = data.is_days_multiplier
        if data.is_active is not None:
            config.is_active = data.is_active
        config.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(config)

    return {
        "config_id": config.config_id,
        "role": config.role,
        "value": config.value,
        "is_days_multiplier": config.is_days_multiplier,
        "is_active": config.is_active,
    }


@router.post("/employee-assignment", status_code=201, summary="Assign rest allowance to an employee")
def assign_employee_rest_allowance(
    data: EmployeeRestAllowanceCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    """Assign extra rest allowance to an employee by badge number."""
    user = db.query(User).filter(User.badge_number == data.badge_number).first()
    if not user:
        raise HTTPException(status_code=404, detail="Employee not found with that badge number")

    assignment = EmployeeRestAllowance(
        user_id=user.user_id,
        assigned_by=current_user.user_id,
        value=data.value,
        is_days_multiplier=data.is_days_multiplier,
        month_year=data.month_year,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)

    return {
        "assignment_id": assignment.assignment_id,
        "user_id": assignment.user_id,
        "name": user.name,
        "badge_number": user.badge_number,
        "value": assignment.value,
        "is_days_multiplier": assignment.is_days_multiplier,
        "month_year": assignment.month_year,
    }


@router.get("/employee-assignment", summary="Get all employee rest allowance assignments")
def list_employee_assignments(
    month_year: Optional[str] = Query(None, description="Format YYYY-MM"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    """Get employee rest allowance assignments (optionally filtered by month)."""
    q = db.query(EmployeeRestAllowance, User).join(User, EmployeeRestAllowance.user_id == User.user_id)
    
    if month_year:
        # Get permanent overrides (month_year is NULL) or specifically for this month
        q = q.filter(or_(
            EmployeeRestAllowance.month_year == month_year,
            EmployeeRestAllowance.month_year == None
        ))
        
    results = q.all()
    
    return {
        "assignments": [
            {
                "assignment_id": assignment.assignment_id,
                "user_id": user.user_id,
                "name": user.name,
                "badge_number": user.badge_number,
                "value": assignment.value,
                "is_days_multiplier": assignment.is_days_multiplier,
                "month_year": assignment.month_year,
                "created_at": assignment.created_at.isoformat(),
            }
            for assignment, user in results
        ]
    }
