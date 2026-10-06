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
