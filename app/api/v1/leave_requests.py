"""
SecureTrack â€” Leave Requests API
Guard tells Leader manually â†’ Leader submits via app â†’ Supervisor (normal) or â†’ Ops Manager â†’ HR (exceptional).
Guard can also submit directly.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime, timezone
import uuid

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.leave_request import LeaveRequest
from app.models.user import User
from app.core.audit import log_audit, log_create, log_update, log_delete, log_read, snapshot

router = APIRouter()


# â”€â”€ Schemas â”€â”€
class LeaveRequestCreate(BaseModel):
    guard_id: str
    guard_name: str
    site_id: str
    site_name: str
    leave_type: str = "normal"  # normal / exceptional
    start_date: date
    end_date: date
    reason: Optional[str] = None


class LeaveRequestAction(BaseModel):
    action: str  # approve / reject
    notes: Optional[str] = None


class LeaveRequestResponse(BaseModel):
    leave_id: str
    guard_id: str
    guard_name: str
    submitted_by: str
    submitted_by_name: str
    site_id: str
    site_name: str
    leave_type: str
    start_date: date
    end_date: date
    reason: Optional[str]
    status: str
    leader_id: Optional[str]
    leader_notes: Optional[str]
    leader_reviewed_at: Optional[datetime]
    supervisor_id: Optional[str]
    supervisor_notes: Optional[str]
    supervisor_reviewed_at: Optional[datetime]
    ops_manager_id: Optional[str]
    ops_manager_notes: Optional[str]
    ops_manager_reviewed_at: Optional[datetime]
    hr_id: Optional[str]
    hr_notes: Optional[str]
    hr_reviewed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# â”€â”€ Endpoints â”€â”€

@router.post("/", response_model=LeaveRequestResponse, status_code=status.HTTP_201_CREATED)
def create_leave_request(
    payload: LeaveRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a leave request. Leader submits on behalf of guard (primary), or guard submits directly."""
    allowed_roles = ["leader", "guard", "outdoor", "admin", "supervisor", "lady"]
    if current_user.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Not authorized to submit leave requests")

    # If leader or supervisor submits, auto-approve the leader step
    initial_status = "pending"
    leader_id = None
    leader_reviewed_at = None
    if current_user.role == "leader":
        initial_status = "approved_by_leader"
        leader_id = current_user.user_id
        leader_reviewed_at = datetime.now(timezone.utc)
    elif current_user.role == "supervisor":
        # Supervisor can create leave for self, guard, or leader
        # Auto-approve leader step since supervisor outranks leader
        initial_status = "approved_by_leader"
        leader_id = current_user.user_id
        leader_reviewed_at = datetime.now(timezone.utc)

    leave = LeaveRequest(
        leave_id=str(uuid.uuid4()),
        guard_id=payload.guard_id,
        guard_name=payload.guard_name,
        submitted_by=current_user.user_id,
        submitted_by_name=current_user.name,
        site_id=payload.site_id,
        site_name=payload.site_name,
        leave_type=payload.leave_type,
        start_date=payload.start_date,
        end_date=payload.end_date,
        reason=payload.reason,
        status=initial_status,
        leader_id=leader_id,
        leader_reviewed_at=leader_reviewed_at,
    )
    db.add(leave)
    db.commit()
    db.refresh(leave)
    return leave


@router.get("/", response_model=List[LeaveRequestResponse])
def list_leave_requests(
    site_id: Optional[str] = None,
    status_filter: Optional[str] = None,
    guard_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List leave requests filtered by role."""
    query = db.query(LeaveRequest)

    if current_user.role == "leader":
        if site_id:
            query = query.filter(LeaveRequest.site_id == site_id)
    elif current_user.role == "supervisor":
        query = query.filter(LeaveRequest.status.in_(["approved_by_leader", "approved_by_supervisor"]))
    elif current_user.role == "operations_manager":
        query = query.filter(LeaveRequest.status == "approved_by_supervisor")
        query = query.filter(LeaveRequest.leave_type == "exceptional")
    elif current_user.role in ("guard", "outdoor"):
        query = query.filter(LeaveRequest.guard_id == current_user.user_id)

    if status_filter:
        query = query.filter(LeaveRequest.status == status_filter)
    if guard_id:
        query = query.filter(LeaveRequest.guard_id == guard_id)

    return query.order_by(LeaveRequest.created_at.desc()).all()


@router.get("/{leave_id}", response_model=LeaveRequestResponse)
def get_leave_request(
    leave_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    leave = db.query(LeaveRequest).filter(LeaveRequest.leave_id == leave_id).first()
    if not leave:
        raise HTTPException(status_code=404, detail="Leave request not found")
    return leave


@router.patch("/{leave_id}/action", response_model=LeaveRequestResponse)
def action_leave_request(
    leave_id: str,
    payload: LeaveRequestAction,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approve or reject a leave request based on current user's role in the chain."""
    leave = db.query(LeaveRequest).filter(LeaveRequest.leave_id == leave_id).first()
    if not leave:
        raise HTTPException(status_code=404, detail="Leave request not found")

    now = datetime.now(timezone.utc)

    if payload.action == "reject":
        leave.status = "rejected"
        # Record who rejected
        if current_user.role == "leader":
            leave.leader_id = current_user.user_id
            leave.leader_notes = payload.notes
            leave.leader_reviewed_at = now
        elif current_user.role == "supervisor":
            leave.supervisor_id = current_user.user_id
            leave.supervisor_notes = payload.notes
            leave.supervisor_reviewed_at = now
        elif current_user.role == "operations_manager":
            leave.ops_manager_id = current_user.user_id
            leave.ops_manager_notes = payload.notes
            leave.ops_manager_reviewed_at = now
        elif current_user.role == "hr":
            leave.hr_id = current_user.user_id
            leave.hr_notes = payload.notes
            leave.hr_reviewed_at = now

    elif payload.action == "approve":
        if current_user.role == "leader" and leave.status == "pending":
            leave.status = "approved_by_leader"
            leave.leader_id = current_user.user_id
            leave.leader_notes = payload.notes
            leave.leader_reviewed_at = now

        elif current_user.role == "supervisor" and leave.status == "approved_by_leader":
            if leave.leave_type == "normal":
                leave.status = "approved"  # FINAL for normal/short leave (2-step)
            else:
                leave.status = "approved_by_supervisor"  # Continues to ops_mgr for exceptional
            leave.supervisor_id = current_user.user_id
            leave.supervisor_notes = payload.notes
            leave.supervisor_reviewed_at = now

        elif current_user.role == "operations_manager" and leave.status == "approved_by_supervisor":
            leave.status = "approved_by_ops_mgr"
            leave.ops_manager_id = current_user.user_id
            leave.ops_manager_notes = payload.notes
            leave.ops_manager_reviewed_at = now

        elif current_user.role == "hr" and leave.status == "approved_by_ops_mgr":
            leave.status = "approved_by_hr"  # Final for exceptional leave
            leave.hr_id = current_user.user_id
            leave.hr_notes = payload.notes
            leave.hr_reviewed_at = now

        elif current_user.role == "admin":
            leave.status = "approved_by_hr"
            leave.hr_id = current_user.user_id
            leave.hr_notes = payload.notes
            leave.hr_reviewed_at = now
        else:
            raise HTTPException(status_code=400, detail="Cannot approve at this stage with your role")

    db.commit()
    db.refresh(leave)
    return leave

