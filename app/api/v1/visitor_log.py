"""
SecureTrack Platform — Visitor Log API Routes
Leader submits visitor entries; Admin manages visit reasons; Client reads logs.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user, require_role
from app.models.user import User
from app.enums import UserRole
from app.schemas.visitor_log import (
    VisitorLogCreate, VisitorLogResponse, VisitorLogListResponse,
    VisitReasonCreate, VisitReasonUpdate, VisitReasonResponse,
)
from app.services.visitor_log_service import VisitorLogService

router = APIRouter()


def _reason_to_resp(r) -> VisitReasonResponse:
    return VisitReasonResponse(
        reason_id=r.reason_id,
        tenant_id=r.tenant_id,
        label=r.label,
        is_active=r.is_active,
        created_at=r.created_at,
    )


def _log_to_resp(log) -> VisitorLogResponse:
    return VisitorLogResponse(
        log_id=log.log_id,
        tenant_id=log.tenant_id,
        leader_id=log.leader_id,
        leader_name=log.leader_name,
        site_id=log.site_id,
        site_name=log.site_name,
        visitor_name=log.visitor_name,
        visitor_id_number=log.visitor_id_number,
        visit_reason=log.visit_reason,
        visitor_photo_url=log.visitor_photo_url,
        id_photo_url=log.id_photo_url,
        notes=log.notes,
        created_at=log.created_at,
    )


# ── Visit Reasons (Admin-managed per tenant) ──────────────────────────────────

@router.get(
    "/reasons",
    response_model=list[VisitReasonResponse],
    summary="List visit reasons for current user's tenant",
)
def list_reasons(
    tenant_id: Optional[str] = Query(None, description="Required for admins not assigned to a tenant"),
    include_inactive: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Any authenticated user can fetch the active visit reasons for their tenant."""
    target_tenant = tenant_id or getattr(current_user, "tenant_id", None)
    if not target_tenant:
        return []
    return [
        _reason_to_resp(r)
        for r in VisitorLogService.list_reasons(
            db, target_tenant,
            include_inactive=include_inactive and current_user.role == UserRole.ADMIN,
        )
    ]


@router.post(
    "/reasons",
    response_model=VisitReasonResponse,
    status_code=201,
    summary="Create a visit reason (admin only)",
)
def create_reason(
    data: VisitReasonCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    target_tenant = data.tenant_id or current_user.tenant_id
    if not target_tenant:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="tenant_id is required")
    reason = VisitorLogService.create_reason(db, target_tenant, data)
    return _reason_to_resp(reason)


@router.put(
    "/reasons/{reason_id}",
    response_model=VisitReasonResponse,
    summary="Update a visit reason (admin only)",
)
def update_reason(
    reason_id: str,
    data: VisitReasonUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    reason = VisitorLogService.update_reason(db, reason_id, current_user.tenant_id, data)
    if not reason:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Visit reason not found")
    return _reason_to_resp(reason)


@router.delete(
    "/reasons/{reason_id}",
    status_code=204,
    summary="Delete a visit reason (admin only)",
)
def delete_reason(
    reason_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    VisitorLogService.delete_reason(db, reason_id, current_user.tenant_id)


# ── Visitor Logs ──────────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=VisitorLogResponse,
    status_code=201,
    summary="Create a visitor log entry (leader)",
)
def create_visitor_log(
    data: VisitorLogCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Leader submits a visitor log entry for their tenant."""
    target_tenant = data.tenant_id or current_user.tenant_id
    if not target_tenant:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="tenant_id is required")
    
    log = VisitorLogService.create_log(
        db,
        tenant_id=target_tenant,
        leader_id=current_user.user_id,
        leader_name=current_user.full_name or current_user.username,
        data=data,
    )
    return _log_to_resp(log)


@router.get(
    "/",
    response_model=VisitorLogListResponse,
    summary="List visitor logs with filters",
)
def list_visitor_logs(
    tenant_id: Optional[str] = Query(None, description="Required for admins not assigned to a tenant"),
    site_id: Optional[str] = Query(None),
    visit_reason: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Leader/Client/Admin can list visitor logs for their tenant with optional filters."""
    target_tenant = tenant_id or current_user.tenant_id
    if not target_tenant:
        return VisitorLogListResponse(logs=[], total=0, page=page, page_size=page_size)

    logs, total = VisitorLogService.list_logs(
        db,
        tenant_id=target_tenant,
        site_id=site_id,
        visit_reason=visit_reason,
        date_from=date_from,
        date_to=date_to,
        search=search,
        page=page,
        page_size=page_size,
    )
    return VisitorLogListResponse(
        logs=[_log_to_resp(log) for log in logs],
        total=total,
        page=page,
        page_size=page_size,
    )
