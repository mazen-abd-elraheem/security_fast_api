"""
SecureTrack Platform — Uniform Routes
Endpoints for uniform item tracking: issue, update, return, and admin tracker views.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.api.deps import get_current_user, require_role, handle_service_exception
from app.models.user import User
from app.enums import UserRole
from app.schemas.uniform import (
    UniformItemCreate, UniformItemUpdate, UniformBulkIssue,
    UniformItemResponse, UniformItemListResponse,
    EmployeeUniformSummary, UniformTrackerResponse,
)
from app.services.uniform_service import UniformService
from app.core.exceptions import SecureTrackException
from app.core.audit import log_audit, log_create, log_update, log_delete, log_read, snapshot

router = APIRouter()


# ── Issue a uniform item ──
@router.post("", response_model=UniformItemResponse, status_code=201, summary="Issue uniform item")
def issue_uniform_item(
    data: UniformItemCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.PERSONNEL_OFFICER, UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Issue a uniform item to an employee. Admin only."""
    try:
        item = UniformService.issue_item(db, data, issued_by_id=current_user.user_id)
        return UniformService.to_response(item)
    except SecureTrackException as e:
        handle_service_exception(e)


# ── Bulk issue uniform items ──
@router.post("/bulk", response_model=UniformItemListResponse, status_code=201, summary="Bulk issue uniforms")
def bulk_issue_uniforms(
    data: UniformBulkIssue,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.PERSONNEL_OFFICER, UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Issue multiple uniform items to one employee at once. Admin only."""
    try:
        items = UniformService.bulk_issue(
            db, data.employee_id, data.items, issued_by_id=current_user.user_id,
        )
        return UniformItemListResponse(
            items=[UniformService.to_response(i) for i in items],
            total=len(items),
        )
    except SecureTrackException as e:
        handle_service_exception(e)


# ── Admin tracker: employees without uniform + terminated unreturned ──
@router.get("/tracker", response_model=UniformTrackerResponse, summary="Uniform tracker dashboard")
def get_uniform_tracker(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Admin dashboard: active employees missing uniforms + terminated with unreturned items."""
    without = UniformService.get_employees_without_uniform(db)
    terminated = UniformService.get_terminated_with_unreturned(db)

    # Build response for terminated employees
    terminated_summaries = []
    for t in terminated:
        terminated_summaries.append(EmployeeUniformSummary(
            employee_id=t["employee_id"],
            employee_name=t["employee_name"],
            badge_number=t["badge_number"],
            role=t["role"],
            is_active=t["is_active"],
            total_items_issued=t["total_items_issued"],
            total_items_returned=t["total_items_returned"],
            total_items_outstanding=t["total_items_outstanding"],
            items=[UniformService.to_response(i) for i in t["items"]],
        ))

    without_summaries = [EmployeeUniformSummary(**w) for w in without]

    return UniformTrackerResponse(
        employees_without_uniform=without_summaries,
        terminated_with_unreturned=terminated_summaries,
        total_without_uniform=len(without_summaries),
        total_terminated_unreturned=len(terminated_summaries),
    )


# ── Get my uniform items (for guards/outdoor/supervisors) ──
@router.get("/my", response_model=UniformItemListResponse, summary="My uniform items")
def get_my_uniforms(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the current user's uniform items (any role)."""
    items = UniformService.get_employee_items(db, current_user.user_id)
    return UniformItemListResponse(
        items=[UniformService.to_response(i) for i in items],
        total=len(items),
    )


# ── Get uniform items for a specific employee (admin) ──
@router.get("/employee/{employee_id}", response_model=UniformItemListResponse, summary="Employee uniforms")
def get_employee_uniforms(
    employee_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.PERSONNEL_OFFICER)),
    db: Session = Depends(get_db),
):
    """Get all uniform items for a specific employee. Admin only."""
    items = UniformService.get_employee_items(db, employee_id)
    return UniformItemListResponse(
        items=[UniformService.to_response(i) for i in items],
        total=len(items),
    )


# ── Get all uniform items (admin, with optional filters) ──
@router.get("", response_model=UniformItemListResponse, summary="List all uniform items")
def list_uniform_items(
    status: Optional[str] = Query(None, description="Filter by status: issued, returned, lost, damaged"),
    employee_id: Optional[str] = Query(None, description="Filter by employee ID"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.PERSONNEL_OFFICER)),
    db: Session = Depends(get_db),
):
    """List all uniform items with optional filters. Admin only."""
    items = UniformService.get_all_items(db, status_filter=status, employee_id=employee_id)
    return UniformItemListResponse(
        items=[UniformService.to_response(i) for i in items],
        total=len(items),
    )


# ── Update a uniform item ──
@router.put("/{item_id}", response_model=UniformItemResponse, summary="Update uniform item")
def update_uniform_item(
    item_id: str,
    data: UniformItemUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.PERSONNEL_OFFICER, UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Update a uniform item (e.g., mark as returned, update size). Admin only."""
    try:
        item = UniformService.update_item(db, item_id, data)
        return UniformService.to_response(item)
    except SecureTrackException as e:
        handle_service_exception(e)


# ── Delete a uniform item ──
@router.delete("/{item_id}", status_code=200, summary="Delete uniform item")
def delete_uniform_item(
    item_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.PERSONNEL_OFFICER)),
    db: Session = Depends(get_db),
):
    """Delete a uniform item record. Admin only."""
    try:
        UniformService.delete_item(db, item_id)
        return {"detail": "Uniform item deleted"}
    except SecureTrackException as e:
        handle_service_exception(e)
