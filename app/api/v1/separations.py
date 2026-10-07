from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User
from app.models.separation_request import SeparationRequest
from app.enums import UserRole

router = APIRouter()

@router.get("/terminated")
def get_terminated_employees(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR))
):
    """
    Get all terminated employees.
    """
    # Fetch inactive users or users with status 'terminated'
    terminated_users = db.query(User).filter((User.status == "terminated") | (User.is_active == False)).all()
    results = []
    
    for emp in terminated_users:
        # Get latest separation reason
        sep = db.query(SeparationRequest).filter(SeparationRequest.user_id == emp.user_id).order_by(SeparationRequest.created_at.desc()).first()
        reason = sep.reason if sep else "غير محدد"
        supervisor_name = "غير محدد"
        if sep and sep.supervisor_id:
            sup = db.query(User).filter(User.user_id == sep.supervisor_id).first()
            if sup:
                supervisor_name = sup.name
            
        results.append({
            "user_id": emp.user_id,
            "employee_code": emp.employee_code or "-",
            "name": emp.name,
            "supervisor": supervisor_name,
            "reason": reason
        })
    return results

@router.post("/{user_id}/request-return")
def request_return_employee(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR))
):
    """
    Request the return of a terminated employee.
    """
    emp = db.query(User).filter(User.user_id == user_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
        
    emp.status = "active"
    emp.is_active = True
    db.commit()
    return {"message": "Employee returned to active status"}

@router.post("/import-row")
def import_terminated_return_row(
    row: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR))
):
    """
    Import an Excel row for returning a terminated employee.
    """
    badge = row.get("badge_number")
    if not badge:
        raise HTTPException(status_code=400, detail="No badge number provided")
        
    emp = db.query(User).filter(User.employee_code == str(badge)).first()
    if emp:
        emp.status = "active"
        emp.is_active = True
        db.commit()
        return {"status": "success", "message": f"User {emp.name} reactivated"}
    else:
        raise HTTPException(status_code=404, detail=f"Employee with code {badge} not found")

from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid

class SeparationCreate(BaseModel):
    user_id: str
    user_name: str
    badge_number: Optional[str] = None
    employee_code: Optional[str] = None
    site_id: str
    site_name: str
    separation_type: str
    reason: str
    requested_last_working_day: Optional[datetime] = None
    actual_last_working_day: Optional[datetime] = None
    status: Optional[str] = "pending_leader"
    financial_settlement: Optional[float] = 0.0
    assets_returned: Optional[bool] = False
    uniform_returned: Optional[bool] = False

class SeparationAction(BaseModel):
    action: str
    notes: Optional[str] = None

@router.post("")
def create_separation(
    req: SeparationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.LEADER, UserRole.SUPERVISOR, UserRole.PERSONNEL, UserRole.ADMIN, UserRole.HR))
):
    sep = SeparationRequest(
        separation_id=str(uuid.uuid4()),
        user_id=req.user_id,
        user_name=req.user_name,
        employee_code=req.badge_number or req.employee_code,
        site_id=req.site_id,
        site_name=req.site_name,
        separation_type=req.separation_type,
        reason=req.reason,
        requested_last_working_day=req.requested_last_working_day,
        actual_last_working_day=req.actual_last_working_day,
        status=req.status,
        financial_settlement=req.financial_settlement,
        assets_returned=req.assets_returned,
        uniform_returned=req.uniform_returned,
        initiated_by=current_user.user_id,
        initiated_by_name=current_user.name
    )
    if req.status == "completed":
        emp = db.query(User).filter(User.user_id == req.user_id).first()
        if emp:
            emp.status = "terminated"
            emp.is_active = False

    db.add(sep)
    db.commit()
    db.refresh(sep)
    return sep

@router.get("")
def get_separations(
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.LEADER, UserRole.SUPERVISOR, UserRole.OPS_MANAGER, UserRole.PERSONNEL, UserRole.ADMIN, UserRole.HR, UserRole.CEO))
):
    query = db.query(SeparationRequest)
    if status_filter:
        query = query.filter(SeparationRequest.status == status_filter)
    if current_user.role in [UserRole.LEADER, UserRole.SUPERVISOR]:
        if current_user.assigned_sites:
            query = query.filter(SeparationRequest.site_id.in_(current_user.assigned_sites))
    return query.order_by(SeparationRequest.created_at.desc()).all()

@router.patch("/{separation_id}/action")
def action_separation(
    separation_id: str,
    action: SeparationAction,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.OPS_MANAGER, UserRole.HR, UserRole.ADMIN, UserRole.PERSONNEL))
):
    sep = db.query(SeparationRequest).filter(SeparationRequest.separation_id == separation_id).first()
    if not sep:
        raise HTTPException(status_code=404, detail="Separation not found")

    if current_user.role == UserRole.SUPERVISOR:
        sep.supervisor_id = current_user.user_id
        sep.supervisor_notes = action.notes
        sep.supervisor_reviewed_at = datetime.now()
        sep.status = "pending_ops_mgr" if action.action == "approve" else "rejected"
        if action.action == "approve":
            sep.uniform_returned = True
            sep.uniform_return_confirmed_by = current_user.user_id
    elif current_user.role == UserRole.OPS_MANAGER:
        sep.ops_manager_id = current_user.user_id
        sep.ops_manager_notes = action.notes
        sep.ops_manager_reviewed_at = datetime.now()
        sep.status = "pending_hr" if action.action == "approve" else "rejected"
    elif current_user.role in [UserRole.HR, UserRole.PERSONNEL, UserRole.ADMIN]:
        sep.hr_id = current_user.user_id
        sep.hr_notes = action.notes
        sep.hr_reviewed_at = datetime.now()
        sep.status = "completed" if action.action == "approve" else "rejected"
        if action.action == "approve":
            emp = db.query(User).filter(User.user_id == sep.user_id).first()
            if emp:
                emp.status = "terminated"
                emp.is_active = False

    db.commit()
    db.refresh(sep)
    return sep
