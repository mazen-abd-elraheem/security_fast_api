"""
SecureTrack — Rest Allowance Config API
Admin/Accountant can configure rest allowance rates per role.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User
from app.models.rest_allowance_config import RestAllowanceConfig
from app.enums import UserRole

router = APIRouter()


# ── Schemas ──

class RestAllowanceConfigCreate(BaseModel):
    role: str
    rate_per_day: float = Field(..., ge=0)


class RestAllowanceConfigUpdate(BaseModel):
    rate_per_day: Optional[float] = Field(None, ge=0)
    is_active: Optional[bool] = None


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
                "rate_per_day": c.rate_per_day,
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
        rate_per_day=data.rate_per_day,
    )
    db.add(config)
    db.commit()
    db.refresh(config)

    return {
        "config_id": config.config_id,
        "role": config.role,
        "rate_per_day": config.rate_per_day,
        "is_active": config.is_active,
    }


@router.put("/config/{role}", summary="Update rest allowance config for a role")
def update_config(
    role: str,
    data: RestAllowanceConfigUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    """Update the rest allowance rate for a specific role."""
    config = db.query(RestAllowanceConfig).filter(
        RestAllowanceConfig.role == role
    ).first()

    if not config:
        # Auto-create if not found
        config = RestAllowanceConfig(
            config_id=str(uuid.uuid4()),
            role=role,
            rate_per_day=data.rate_per_day if data.rate_per_day is not None else 0.0,
            is_active=data.is_active if data.is_active is not None else True,
        )
        db.add(config)
    else:
        if data.rate_per_day is not None:
            config.rate_per_day = data.rate_per_day
        if data.is_active is not None:
            config.is_active = data.is_active
        config.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(config)

    return {
        "config_id": config.config_id,
        "role": config.role,
        "rate_per_day": config.rate_per_day,
        "is_active": config.is_active,
    }
