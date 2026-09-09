"""
SecureTrack Platform — Vacation Requests API
Handles creation (by supervisor) and approval (by ops manager) of vacation requests.
"""
import os
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, UploadFile, File, Query, HTTPException, status, Form
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user, require_role
from app.models.user import User
from app.models.site import Site
from app.models.vacation_request import VacationRequest
from app.enums import UserRole
from app.core.config import settings

router = APIRouter()

UPLOAD_DIR = os.path.join(settings.UPLOAD_DIR, "vacation_requests")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("", status_code=201, summary="Create a vacation request")
async def create_vacation_request(
    user_id: str = Form(...),
    site_id: str = Form(...),
    days_count: int = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.LEADER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Supervisor creates a vacation request for a guard/employee."""
    
    # Verify target user
    target_user = db.query(User).filter(User.user_id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user not found")
        
    # Verify site
    site = db.query(Site).filter(Site.site_id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    # Generate unique filename for the picture
    ext = os.path.splitext(file.filename or "request.jpg")[1] or ".jpg"
    filename = f"vacation_{user_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    # Save file
    contents = await file.read()
    with open(filepath, "wb") as f:
        f.write(contents)

    # Construct the image URL. Assumes main static file serving is mapped at /static/uploads
    image_url = f"/static/uploads/vacation_requests/{filename}"

    request_id = str(uuid.uuid4())
    vacation = VacationRequest(
        request_id=request_id,
        user_id=target_user.user_id,
        user_name=target_user.name,
        employee_code=target_user.employee_code,
        site_id=site.site_id,
        site_name=site.name,
        supervisor_id=current_user.user_id,
        supervisor_name=current_user.name,
        days_count=days_count,
        image_url=image_url,
        status="pending_ops_mgr",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    
    db.add(vacation)
    db.commit()
    
    return {"message": "Request created successfully", "request_id": request_id}


@router.get("", summary="Get vacation requests")
def get_vacation_requests(
    status_filter: Optional[str] = Query(None, alias="status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch vacation requests. Admin/Ops see all. Supervisors see requests they made."""
    query = db.query(VacationRequest)
    
    # Filter by role
    if current_user.role in [UserRole.SUPERVISOR, UserRole.LEADER]:
        query = query.filter(VacationRequest.supervisor_id == current_user.user_id)
        
    # Filter by status
    if status_filter:
        query = query.filter(VacationRequest.status == status_filter)
        
    requests = query.order_by(VacationRequest.created_at.desc()).all()
    
    return {
        "requests": [
            {
                "request_id": r.request_id,
                "user_id": r.user_id,
                "user_name": r.user_name,
                "employee_code": r.employee_code,
                "site_id": r.site_id,
                "site_name": r.site_name,
                "supervisor_id": r.supervisor_id,
                "supervisor_name": r.supervisor_name,
                "days_count": r.days_count,
                "image_url": r.image_url,
                "status": r.status,
                "ops_manager_id": r.ops_manager_id,
                "ops_manager_notes": r.ops_manager_notes,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in requests
        ]
    }


@router.patch("/{request_id}/action", summary="Approve or Reject vacation request")
def action_vacation_request(
    request_id: str,
    action: str = Query(..., description="'approve' or 'reject'"),
    notes: Optional[str] = Query(None),
    current_user: User = Depends(require_role(UserRole.OPS_MANAGER, UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """Ops Manager approves or rejects the request."""
    vacation = db.query(VacationRequest).filter(VacationRequest.request_id == request_id).first()
    if not vacation:
        raise HTTPException(status_code=404, detail="Request not found")
        
    if action == "approve":
        vacation.status = "approved"
    elif action == "reject":
        vacation.status = "rejected"
    else:
        raise HTTPException(status_code=400, detail="Invalid action. Use 'approve' or 'reject'")
        
    vacation.ops_manager_id = current_user.user_id
    vacation.ops_manager_notes = notes
    vacation.reviewed_at = datetime.now(timezone.utc)
    vacation.updated_at = datetime.now(timezone.utc)
    
    db.commit()
    
    return {"message": f"Request {action}d successfully", "status": vacation.status}
