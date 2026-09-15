"""
SecureTrack Platform - Transfer Methods API
Handles CRUD for dynamic transfer methods (payment channels).
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
import uuid

from app.core.database import get_db
from app.api.deps import require_role
from app.enums import UserRole
from app.models.user import User
from app.models.accountant_models import TransferMethod

router = APIRouter()


class TransferMethodCreate(BaseModel):
    name: str
    name_ar: Optional[str] = None
    is_active: bool = True
    sort_order: int = 0


class TransferMethodUpdate(BaseModel):
    name: Optional[str] = None
    name_ar: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


@router.get("", summary="Get all transfer methods")
def get_transfer_methods(
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    methods = db.query(TransferMethod).order_by(TransferMethod.sort_order.asc(), TransferMethod.name.asc()).all()
    return {
        "methods": [
            {
                "id": m.id,
                "name": m.name,
                "name_ar": m.name_ar,
                "is_active": m.is_active,
                "sort_order": m.sort_order,
            }
            for m in methods
        ]
    }


@router.post("", status_code=201, summary="Create a transfer method")
def create_transfer_method(
    payload: TransferMethodCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    method = TransferMethod(
        id=str(uuid.uuid4()),
        name=payload.name,
        name_ar=payload.name_ar,
        is_active=payload.is_active,
        sort_order=payload.sort_order,
    )
    db.add(method)
    db.commit()
    db.refresh(method)
    return {"message": "Transfer method created successfully", "id": method.id}


@router.put("/{method_id}", summary="Update a transfer method")
def update_transfer_method(
    method_id: str,
    payload: TransferMethodUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    method = db.query(TransferMethod).filter(TransferMethod.id == method_id).first()
    if not method:
        raise HTTPException(status_code=404, detail="Transfer method not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(method, field, value)

    db.commit()
    return {"message": "Transfer method updated successfully"}


@router.delete("/{method_id}", summary="Delete a transfer method")
def delete_transfer_method(
    method_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    method = db.query(TransferMethod).filter(TransferMethod.id == method_id).first()
    if not method:
        raise HTTPException(status_code=404, detail="Transfer method not found")

    db.delete(method)
    db.commit()
    return {"message": "Transfer method deleted successfully"}
