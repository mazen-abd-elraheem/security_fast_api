"""
SecureTrack Platform — Annual Leave Balance API
Admin management of 21-day annual leave entitlement per employee.
Employees become eligible after 3 months of hire date.
"""
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user, require_role
from app.models.user import User
from app.models.annual_leave_balance import AnnualLeaveBalance
from app.enums import UserRole

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _add_months(d: date, months: int) -> date:
    """Add a number of months to a date using only stdlib."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    days_in_month = [31, 28 + (1 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 0),
                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    day = min(d.day, days_in_month[month - 1])
    return date(year, month, day)


def _compute_eligible_from(hire_date) -> Optional[date]:
    """Returns hire_date + 3 months, the date employee becomes eligible."""
    if not hire_date:
        return None
    if isinstance(hire_date, datetime):
        hire_date = hire_date.date()
    try:
        return _add_months(hire_date, 3)
    except Exception:
        return None


def _get_or_create_balance(db: Session, employee_id: str, year: int, hire_date=None) -> AnnualLeaveBalance:
    """Get existing balance or create one for the given year."""
    bal = db.query(AnnualLeaveBalance).filter(
        AnnualLeaveBalance.employee_id == employee_id,
        AnnualLeaveBalance.year == year,
    ).first()
    if not bal:
        eligible_from = _compute_eligible_from(hire_date)
        bal = AnnualLeaveBalance(
            id=str(uuid.uuid4()),
            employee_id=employee_id,
            year=year,
            total_days=21,
            used_days=0,
            eligible_from=eligible_from,
        )
        db.add(bal)
        db.commit()
        db.refresh(bal)
    return bal


def _is_eligible(bal: AnnualLeaveBalance) -> bool:
    """True if today >= eligible_from."""
    if not bal.eligible_from:
        return False
    return date.today() >= bal.eligible_from


def _balance_dict(bal: AnnualLeaveBalance, user: User) -> dict:
    eligible = _is_eligible(bal)
    return {
        "employee_id": bal.employee_id,
        "employee_name": user.name if user else "Unknown",
        "year": bal.year,
        "total_days": bal.total_days,
        "used_days": bal.used_days,
        "remaining_days": max(0, bal.total_days - bal.used_days),
        "eligible": eligible,
        "eligible_from": bal.eligible_from.isoformat() if bal.eligible_from else None,
    }


# ── Schemas ───────────────────────────────────────────────────────────────────

class LeaveBalanceUpdate(BaseModel):
    total_days: int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/employees", summary="List all employees with annual leave balances")
def list_employee_balances(
    year: int = Query(default=None, description="Year (defaults to current year)"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    """List all active employees with their annual leave balance for the given year."""
    if not year:
        year = date.today().year

    LEAVE_ROLES = {"guard", "outdoor", "supervisor", "leader", "lady", "operations_manager",
                   "accountant", "hr", "personnel_officer"}
    employees = db.query(User).filter(User.role.in_(LEAVE_ROLES), User.is_active == True).all()

    result = []
    for emp in employees:
        bal = db.query(AnnualLeaveBalance).filter(
            AnnualLeaveBalance.employee_id == emp.user_id,
            AnnualLeaveBalance.year == year,
        ).first()
        if not bal:
            eligible_from = _compute_eligible_from(emp.hire_date or emp.created_at)
            bal = AnnualLeaveBalance(
                id=str(uuid.uuid4()),
                employee_id=emp.user_id,
                year=year,
                total_days=21,
                used_days=0,
                eligible_from=eligible_from,
            )
            db.add(bal)
        result.append(_balance_dict(bal, emp))

    db.commit()
    return {"year": year, "employees": result, "total": len(result)}


@router.get("/{employee_id}", summary="Get one employee's annual leave balance")
def get_employee_balance(
    employee_id: str,
    year: int = Query(default=None),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR, UserRole.ACCOUNTANT, UserRole.LEADER, UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    if not year:
        year = date.today().year
    emp = db.query(User).filter(User.user_id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    bal = _get_or_create_balance(db, employee_id, year, hire_date=emp.hire_date or emp.created_at)
    return _balance_dict(bal, emp)


@router.put("/{employee_id}", summary="Override employee annual leave total (admin)")
def update_employee_balance(
    employee_id: str,
    payload: LeaveBalanceUpdate,
    year: int = Query(default=None),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    if not year:
        year = date.today().year
    emp = db.query(User).filter(User.user_id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    bal = _get_or_create_balance(db, employee_id, year, hire_date=emp.hire_date or emp.created_at)
    bal.total_days = payload.total_days
    bal.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(bal)
    return _balance_dict(bal, emp)


@router.post("/initialize", summary="Initialize balances for all active employees for a year (idempotent)")
def initialize_year_balances(
    year: int = Query(default=None),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    if not year:
        year = date.today().year

    LEAVE_ROLES = {"guard", "outdoor", "supervisor", "leader", "lady", "operations_manager",
                   "accountant", "hr", "personnel_officer"}
    employees = db.query(User).filter(User.role.in_(LEAVE_ROLES), User.is_active == True).all()

    created = 0
    existing = 0
    for emp in employees:
        bal = db.query(AnnualLeaveBalance).filter(
            AnnualLeaveBalance.employee_id == emp.user_id,
            AnnualLeaveBalance.year == year,
        ).first()
        if not bal:
            eligible_from = _compute_eligible_from(emp.hire_date or emp.created_at)
            db.add(AnnualLeaveBalance(
                id=str(uuid.uuid4()),
                employee_id=emp.user_id,
                year=year,
                total_days=21,
                used_days=0,
                eligible_from=eligible_from,
            ))
            created += 1
        else:
            existing += 1

    db.commit()
    return {"year": year, "created": created, "already_existed": existing}
