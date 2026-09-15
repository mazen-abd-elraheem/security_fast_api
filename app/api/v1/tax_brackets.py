"""
SecureTrack Platform - Tax Brackets API
Handles CRUD for dynamic tax brackets.
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
from app.models.accountant_models import TaxBracket

router = APIRouter()

class TaxBracketCreate(BaseModel):
    min_amount: float
    max_amount: Optional[float] = None
    rate: float
    label: Optional[str] = None
    is_active: bool = True

class TaxBracketUpdate(BaseModel):
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    rate: Optional[float] = None
    label: Optional[str] = None
    is_active: Optional[bool] = None

@router.get("", summary="Get all tax brackets")
def get_tax_brackets(
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    brackets = db.query(TaxBracket).order_by(TaxBracket.min_amount.asc()).all()
    return {
        "brackets": [
            {
                "id": b.id,
                "min_amount": b.min_amount,
                "max_amount": b.max_amount,
                "rate": b.rate,
                "label": b.label,
                "is_active": b.is_active,
            }
            for b in brackets
        ]
    }

@router.post("", status_code=201, summary="Create a tax bracket")
def create_tax_bracket(
    payload: TaxBracketCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    bracket = TaxBracket(
        id=str(uuid.uuid4()),
        min_amount=payload.min_amount,
        max_amount=payload.max_amount,
        rate=payload.rate,
        label=payload.label,
        is_active=payload.is_active,
    )
    db.add(bracket)
    db.commit()
    db.refresh(bracket)
    return {"message": "Tax bracket created successfully", "id": bracket.id}

@router.put("/{bracket_id}", summary="Update a tax bracket")
def update_tax_bracket(
    bracket_id: str,
    payload: TaxBracketUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    bracket = db.query(TaxBracket).filter(TaxBracket.id == bracket_id).first()
    if not bracket:
        raise HTTPException(status_code=404, detail="Tax bracket not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(bracket, field, value)

    db.commit()
    return {"message": "Tax bracket updated successfully"}

@router.delete("/{bracket_id}", summary="Delete a tax bracket")
def delete_tax_bracket(
    bracket_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    bracket = db.query(TaxBracket).filter(TaxBracket.id == bracket_id).first()
    if not bracket:
        raise HTTPException(status_code=404, detail="Tax bracket not found")

    db.delete(bracket)
    db.commit()
    return {"message": "Tax bracket deleted successfully"}
