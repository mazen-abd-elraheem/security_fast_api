"""
SecureTrack — Travel Fees API
Simple table: Site A → Site B = X EGP.
Admin/Ops Manager can CRUD. All authenticated users can read.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import require_role, get_current_user
from app.models.travel_fee import TravelFee
from app.models.user import User
from app.enums import UserRole
from app.core.audit import log_audit, log_create, log_update, log_delete, log_read, snapshot

router = APIRouter()


class TravelFeeCreate(BaseModel):
    from_site_name: str = Field(..., min_length=1)
    to_site_name: str = Field(..., min_length=1)
    amount: float = Field(..., ge=0)


class TravelFeeUpdate(BaseModel):
    from_site_name: Optional[str] = None
    to_site_name: Optional[str] = None
    amount: Optional[float] = None
    is_active: Optional[bool] = None


@router.get("/", summary="List all travel fees")
def list_travel_fees(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all active travel fee routes."""
    fees = db.query(TravelFee).filter(TravelFee.is_active == True).order_by(TravelFee.from_site_name).all()
    return [_fee_to_dict(f) for f in fees]


@router.post("/", status_code=201, summary="Create a travel fee route")
def create_travel_fee(
    data: TravelFeeCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.OPERATIONS_MANAGER)),
    db: Session = Depends(get_db),
):
    """Admin or Ops Manager creates a new travel fee route."""
    fee = TravelFee(
        fee_id=str(uuid.uuid4()),
        from_site_name=data.from_site_name,
        to_site_name=data.to_site_name,
        amount=data.amount,
    )
    db.add(fee)
    db.commit()
    db.refresh(fee)
    return _fee_to_dict(fee)


@router.put("/{fee_id}", summary="Update a travel fee")
def update_travel_fee(
    fee_id: str,
    data: TravelFeeUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.OPERATIONS_MANAGER)),
    db: Session = Depends(get_db),
):
    """Update a travel fee entry."""
    fee = db.query(TravelFee).filter(TravelFee.fee_id == fee_id).first()
    if not fee:
        raise HTTPException(status_code=404, detail="Travel fee not found")

    if data.from_site_name is not None:
        fee.from_site_name = data.from_site_name
    if data.to_site_name is not None:
        fee.to_site_name = data.to_site_name
    if data.amount is not None:
        fee.amount = data.amount
    if data.is_active is not None:
        fee.is_active = data.is_active
    fee.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(fee)
    return _fee_to_dict(fee)


@router.delete("/{fee_id}", summary="Delete a travel fee")
def delete_travel_fee(
    fee_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Soft-delete a travel fee (mark inactive)."""
    fee = db.query(TravelFee).filter(TravelFee.fee_id == fee_id).first()
    if not fee:
        raise HTTPException(status_code=404, detail="Travel fee not found")
    fee.is_active = False
    fee.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "Travel fee deleted"}


def _fee_to_dict(f: TravelFee) -> dict:
    return {
        "fee_id": f.fee_id,
        "from_site_name": f.from_site_name,
        "to_site_name": f.to_site_name,
        "amount": f.amount,
        "is_active": f.is_active,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }
