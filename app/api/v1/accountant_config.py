"""
SecureTrack - Tax Brackets, Bonuses, Holidays, Terminations CRUD API
"""
import uuid
from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user, require_role
from app.models.user import User
from app.models.accountant_models import TaxBracket, EmployeeBonus, Holiday, Termination
from app.enums import UserRole

router = APIRouter()


# -- Tax Bracket Schemas --
class TaxBracketIn(BaseModel):
    min_amount: float = 0
    max_amount: Optional[float] = None
    rate: float = 0
    label: Optional[str] = None

class TaxBracketOut(TaxBracketIn):
    id: str
    is_active: bool = True
    class Config:
        from_attributes = True


@router.get("/brackets", response_model=List[TaxBracketOut])
def list_brackets(
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    return db.query(TaxBracket).filter(TaxBracket.is_active == True).order_by(TaxBracket.min_amount).all()


@router.post("/brackets", response_model=TaxBracketOut)
def add_bracket(
    data: TaxBracketIn,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    bracket = TaxBracket(id=str(uuid.uuid4()), **data.model_dump())
    db.add(bracket)
    db.commit()
    db.refresh(bracket)
    return bracket


@router.put("/brackets/{bracket_id}", response_model=TaxBracketOut)
def update_bracket(
    bracket_id: str,
    data: TaxBracketIn,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    bracket = db.query(TaxBracket).filter(TaxBracket.id == bracket_id).first()
    if not bracket:
        raise HTTPException(status_code=404, detail="Not found")
    for k, v in data.model_dump().items():
        setattr(bracket, k, v)
    db.commit()
    db.refresh(bracket)
    return bracket


@router.delete("/brackets/{bracket_id}")
def delete_bracket(
    bracket_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    bracket = db.query(TaxBracket).filter(TaxBracket.id == bracket_id).first()
    if not bracket:
        raise HTTPException(status_code=404, detail="Not found")
    bracket.is_active = False
    db.commit()
    return {"detail": "Deleted"}


# -- Bonus Schemas --
class BonusIn(BaseModel):
    employee_id: str
    year: int
    month: int
    bonus_type: str
    amount: float = 0
    notes: Optional[str] = None

class BonusOut(BonusIn):
    id: str
    created_by: Optional[str] = None
    class Config:
        from_attributes = True


@router.get("/bonuses", response_model=List[BonusOut])
def list_bonuses(
    year: int = Query(...),
    month: int = Query(...),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    return db.query(EmployeeBonus).filter(EmployeeBonus.year == year, EmployeeBonus.month == month).all()


@router.post("/bonuses", response_model=BonusOut)
def add_bonus(
    data: BonusIn,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    bonus = EmployeeBonus(id=str(uuid.uuid4()), created_by=current_user.user_id, **data.model_dump())
    db.add(bonus)
    db.commit()
    db.refresh(bonus)
    return bonus


@router.delete("/bonuses/{bonus_id}")
def delete_bonus(
    bonus_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    bonus = db.query(EmployeeBonus).filter(EmployeeBonus.id == bonus_id).first()
    if not bonus:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(bonus)
    db.commit()
    return {"detail": "Deleted"}


# -- Holiday Schemas --
class HolidayIn(BaseModel):
    name: str
    date: date
    is_paid: bool = True

class HolidayOut(HolidayIn):
    id: str
    class Config:
        from_attributes = True


@router.get("/holidays", response_model=List[HolidayOut])
def list_holidays(
    year: int = Query(None),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    q = db.query(Holiday)
    if year:
        from sqlalchemy import extract
from app.core.audit import log_audit, log_create, log_update, log_delete, log_read, snapshot
        q = q.filter(extract("year", Holiday.date) == year)
    return q.order_by(Holiday.date).all()


@router.post("/holidays", response_model=HolidayOut)
def add_holiday(
    data: HolidayIn,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    holiday = Holiday(id=str(uuid.uuid4()), **data.model_dump())
    db.add(holiday)
    db.commit()
    db.refresh(holiday)
    return holiday


@router.delete("/holidays/{holiday_id}")
def delete_holiday(
    holiday_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    holiday = db.query(Holiday).filter(Holiday.id == holiday_id).first()
    if not holiday:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(holiday)
    db.commit()
    return {"detail": "Deleted"}


# -- Termination Schemas --
class TerminationIn(BaseModel):
    employee_id: str
    termination_date: date
    reason: Optional[str] = None
    final_settlement: float = 0

class TerminationOut(TerminationIn):
    id: str
    status: str = "pending"
    created_by: Optional[str] = None
    class Config:
        from_attributes = True


@router.get("/terminations", response_model=List[TerminationOut])
def list_terminations(
    status: Optional[str] = None,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    q = db.query(Termination)
    if status:
        q = q.filter(Termination.status == status)
    return q.order_by(Termination.termination_date.desc()).all()


@router.post("/terminations", response_model=TerminationOut)
def add_termination(
    data: TerminationIn,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.user_id == data.employee_id).first()
    if user:
        user.is_active = False
        user.status = "terminated"
    term = Termination(id=str(uuid.uuid4()), created_by=current_user.user_id, **data.model_dump())
    db.add(term)
    db.commit()
    db.refresh(term)
    return term


@router.put("/terminations/{term_id}/approve")
def approve_termination(
    term_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    term = db.query(Termination).filter(Termination.id == term_id).first()
    if not term:
        raise HTTPException(status_code=404, detail="Not found")
    term.status = "approved"
    db.commit()
    return {"detail": "Approved"}
