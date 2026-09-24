"""
SecureTrack Platform — Admin Routes
Admin-only operations: audit logs, system management, user activation.
"""
import uuid
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timezone

from app.core.database import get_db
from app.api.deps import require_role, handle_service_exception
from app.models.user import User
from app.models.admin_audit_log import AdminAuditLog
from app.enums import UserRole
from app.schemas.user import UserResponse, UserListResponse, HRCompleteProfileRequest
from app.services.user_service import UserService
from app.core.exceptions import SecureTrackException
from app.core.audit import log_audit, log_create, log_update, log_delete, log_read, snapshot

router = APIRouter()


@router.get("/audit-logs", summary="Get audit logs")
def get_audit_logs(
    action: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Get admin audit logs with optional filtering."""
    query = db.query(AdminAuditLog)
    if action:
        query = query.filter(AdminAuditLog.action == action)
    if target_type:
        query = query.filter(AdminAuditLog.target_type == target_type)

    # Single query: fetch rows + total via window function avoids double scan
    ordered = query.order_by(AdminAuditLog.created_at.desc())
    logs = ordered.offset(skip).limit(limit).all()
    # Count only if we got a full page (otherwise we already know total)
    total = skip + len(logs) if len(logs) < limit else query.count()

    return {
        "logs": [
            {
                "log_id": l.log_id, "admin_id": l.admin_id, "admin_name": l.admin_name,
                "action": l.action, "target_type": l.target_type, "target_id": l.target_id,
                "target_name": l.target_name, "description": l.description,
                "details": l.details, "severity": l.severity, "created_at": l.created_at,
            }
            for l in logs
        ],
        "total": total,
    }



# ==========================================
# Pending User Activation Management
# ==========================================

@router.get(
    "/pending-users",
    response_model=UserListResponse,
    summary="List users pending activation",
)
def list_pending_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    List all users who have registered but are still pending admin approval.

    Returns users with status=PENDING ordered by registration date (newest first).
    """
    return UserService.list_pending_users(db, skip=skip, limit=limit)


@router.post(
    "/pending-users/{user_id}/approve",
    response_model=UserResponse,
    summary="Approve a pending user",
)
def approve_user(
    user_id: str,
    role: Optional[str] = Query(None, description="Override role on approval"),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Admin approves a pending user registration.

    - Sets status=ACTIVE and is_active=True so the user can now log in.
    - Optionally override the role the user requested.
    - Sends an approval email notification.
    """
    try:
        user = UserService.approve_user(db, user_id, role=role)

        # Audit log
        create_audit_log(
            db, current_user,
            action="approve_user",
            target_type="user",
            target_id=user_id,
            target_name=user.name,
            description=f"Approved user '{user.name}' ({user.email}) with role '{user.role}'",
            severity="info",
        )
        db.commit()

        return user
    except SecureTrackException as e:
        handle_service_exception(e)


@router.post(
    "/pending-users/{user_id}/activate",
    response_model=UserResponse,
    summary="Activate a user",
)
def activate_user(
    user_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Admin activates a user account.

    Sets is_active=True and status=ACTIVE so the user can log in.
    """
    try:
        user = UserService.activate_user(db, user_id)

        # Audit log
        create_audit_log(
            db, current_user,
            action="activate_user",
            target_type="user",
            target_id=user_id,
            target_name=user.name,
            description=f"Activated user '{user.name}' ({user.email})",
            severity="info",
        )
        db.commit()

        return user
    except SecureTrackException as e:
        handle_service_exception(e)


@router.post(
    "/users/{user_id}/complete-onboarding",
    response_model=UserResponse,
    summary="HR completes a fresh user profile",
)
def complete_onboarding(
    user_id: str,
    data: HRCompleteProfileRequest,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """
    HR completes the profile for a 'fresh' user.
    Updates sensitive fields and marks as completed or missing.
    """
    try:
        user = UserService.get_by_id(db, user_id)
        if not user:
            from app.core.exceptions import NotFoundException
            raise NotFoundException("User", user_id)
            
        from app.core.security import hash_password
        
        user.email = data.email
        user.password_hash = hash_password(data.password)
        
        if data.base_salary is not None:
            user.base_salary = data.base_salary
        if data.classification is not None:
            user.classification = data.classification
        if data.bank_account is not None:
            user.bank_account = data.bank_account
        if data.transfer_name is not None:
            user.transfer_name = data.transfer_name
        if data.transfer_method is not None:
            user.transfer_method = data.transfer_method
        if data.national_id is not None:
            user.national_id = data.national_id
        if data.insurance_number is not None:
            user.insurance_number = data.insurance_number
            
        user.onboarding_status = "missing" if data.is_missing_docs else "completed"
        
        if user.onboarding_status == "completed":
            user.is_active = True
            from app.enums import UserStatus
            user.status = UserStatus.ACTIVE if hasattr(UserStatus, 'ACTIVE') else "active"

        create_audit_log(
            db, current_user,
            action="complete_onboarding",
            target_type="user",
            target_id=user_id,
            target_name=user.name,
            description=f"Completed onboarding for '{user.name}' ({user.email}) -> status: {user.onboarding_status}",
            severity="info",
        )
        db.commit()
        db.refresh(user)

        return user
    except SecureTrackException as e:
        handle_service_exception(e)


@router.post(
    "/pending-users/{user_id}/reject",
    summary="Reject a pending user registration",
)
def reject_user(
    user_id: str,
    reason: Optional[str] = Query("", description="Reason for rejection"),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Admin rejects a pending user registration.

    The user record is kept with status=REJECTED.
    An email notification is sent to the user.
    """
    try:
        # Get user info before rejection for audit log
        user = UserService.get_by_id(db, user_id)
        user_name = user.name if user else "Unknown"
        user_email = user.email if user else "Unknown"

        result = UserService.reject_user(db, user_id, reason=reason)

        # Audit log
        create_audit_log(
            db, current_user,
            action="reject_user",
            target_type="user",
            target_id=user_id,
            target_name=user_name,
            description=f"Rejected registration for '{user_name}' ({user_email})",
            severity="warning",
        )
        db.commit()

        return result
    except SecureTrackException as e:
        handle_service_exception(e)


@router.delete(
    "/users/{user_id}",
    summary="Permanently delete a user",
)
def delete_user(
    user_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Admin permanently deletes a user record from the database.
    """
    try:
        user = UserService.get_by_id(db, user_id)
        user_name = user.name if user else "Unknown"
        user_email = user.email if user else "Unknown"

        result = UserService.delete_user(db, user_id)

        create_audit_log(
            db, current_user,
            action="delete_user",
            target_type="user",
            target_id=user_id,
            target_name=user_name,
            description=f"Permanently deleted user '{user_name}' ({user_email})",
            severity="critical",
        )
        db.commit()

        return result
    except SecureTrackException as e:
        handle_service_exception(e)


def create_audit_log(
    db: Session,
    admin: User,
    action: str,
    target_type: str = None,
    target_id: str = None,
    target_name: str = None,
    description: str = None,
    details: dict = None,
    severity: str = "info",
):
    """Helper to create an audit log entry."""
    log = AdminAuditLog(
        log_id=str(uuid.uuid4()),
        admin_id=admin.user_id,
        admin_name=admin.name,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        description=description,
        details=details,
        severity=severity,
    )
    db.add(log)
    db.flush()
    return log



