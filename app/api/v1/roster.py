"""
SecureTrack Platform — Roster Routes
Guard scheduling endpoints.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from datetime import date
from typing import Optional

from app.core.database import get_db
from app.api.deps import get_current_user, require_role, handle_service_exception
from app.models.user import User
from app.enums import UserRole
from app.schemas.roster import RosterCreate, BulkRosterCreate, RosterResponse, RosterListResponse
from app.services.roster_service import RosterService
from app.core.exceptions import SecureTrackException

router = APIRouter()


@router.post("", response_model=RosterResponse, status_code=201, summary="Assign guard to shift")
def assign_guard(
    roster_data: RosterCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Assign a guard to a shift on a specific date."""
    try:
        roster = RosterService.assign_guard(db, roster_data)
        return RosterResponse.model_validate(roster)
    except SecureTrackException as e:
        handle_service_exception(e)


@router.post("/bulk", status_code=201, summary="Bulk assign guards")
def bulk_assign(
    bulk_data: BulkRosterCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Assign multiple guards to shifts at once."""
    try:
        results = RosterService.bulk_assign(db, bulk_data)
        return {"detail": f"{len(results)} assignments created", "count": len(results)}
    except SecureTrackException as e:
        handle_service_exception(e)


@router.get("/site/{site_id}", response_model=RosterListResponse, summary="Get roster for site")
def get_roster_for_site(
    site_id: str,
    target_date: date = Query(..., description="Date to get roster for"),
    current_user: User = Depends(require_role(
        UserRole.ADMIN, UserRole.SUPERVISOR,
    )),
    db: Session = Depends(get_db),
):
    """Get all guard assignments for a site on a specific date."""
    roster = RosterService.get_roster_for_site(db, site_id, target_date)
    items = []
    for r in roster:
        items.append(RosterResponse(
            roster_id=r.roster_id,
            guard_id=r.guard_id,
            guard_name=r.guard.name if r.guard else None,
            guard_badge=r.guard.badge_number if r.guard else None,
            shift_id=r.shift_id,
            site_id=r.shift.site_id if r.shift else None,
            site_name=r.shift.site.name if r.shift and r.shift.site else None,
            shift_label=r.shift.label if r.shift else None,
            shift_start=str(r.shift.start_time) if r.shift else None,
            shift_end=str(r.shift.end_time) if r.shift else None,
            assigned_date=r.assigned_date,
            status=r.status,
            created_at=r.created_at,
        ))
    return RosterListResponse(roster=items, total=len(items))


@router.get("/guard/{guard_id}", response_model=RosterListResponse, summary="Get guard schedule")
def get_guard_schedule(
    guard_id: str,
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a guard's schedule. Guards can view their own; admins can view any."""
    user_role = current_user.role.value if hasattr(current_user.role, 'value') else current_user.role
    if user_role == "guard" and current_user.user_id != guard_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Guards can only view their own schedule")

    roster = RosterService.get_guard_schedule(db, guard_id, date_from, date_to)
    items = []
    for r in roster:
        items.append(RosterResponse(
            roster_id=r.roster_id,
            guard_id=r.guard_id,
            guard_name=r.guard.name if r.guard else None,
            guard_badge=r.guard.badge_number if r.guard else None,
            shift_id=r.shift_id,
            site_id=r.shift.site_id if r.shift else None,
            site_name=r.shift.site.name if r.shift and r.shift.site else None,
            shift_label=r.shift.label if r.shift else None,
            shift_start=str(r.shift.start_time) if r.shift else None,
            shift_end=str(r.shift.end_time) if r.shift else None,
            assigned_date=r.assigned_date,
            status=r.status,
            created_at=r.created_at,
        ))
    return RosterListResponse(roster=items, total=len(items))


@router.delete("/{roster_id}", status_code=200, summary="Remove assignment")
def remove_assignment(
    roster_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Cancel a roster assignment."""
    try:
        RosterService.remove_assignment(db, roster_id)
        return {"detail": "Assignment canceled"}
    except SecureTrackException as e:
        handle_service_exception(e)
