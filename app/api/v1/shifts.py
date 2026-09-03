"""
SecureTrack Platform — Shift Routes
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import require_role, handle_service_exception
from app.models.user import User
from app.enums import UserRole
from app.schemas.shift import ShiftCreate, ShiftUpdate, ShiftResponse, ShiftListResponse
from app.services.shift_service import ShiftService
from app.core.exceptions import SecureTrackException
from app.core.audit import log_audit, log_create, log_update, log_delete, log_read, snapshot

router = APIRouter()


@router.post("/{site_id}/shifts", response_model=ShiftResponse, status_code=201, summary="Create shift")
def create_shift(
    site_id: str,
    shift_data: ShiftCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Create a new shift for a site."""
    shift_data.site_id = site_id
    try:
        shift = ShiftService.create_shift(db, shift_data)
        log_create(db, current_user, "shift", shift)
        db.commit()
        return shift
    except SecureTrackException as e:
        handle_service_exception(e)


@router.get("/{site_id}/shifts", response_model=ShiftListResponse, summary="List shifts for site")
def list_shifts(
    site_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Get all active shifts for a site."""
    try:
        shifts = ShiftService.get_shifts_for_site(db, site_id)
        return ShiftListResponse(shifts=[ShiftResponse.model_validate(s) for s in shifts], total=len(shifts))
    except SecureTrackException as e:
        handle_service_exception(e)


@router.put("/shifts/{shift_id}", response_model=ShiftResponse, summary="Update shift")
def update_shift(
    shift_id: str,
    update_data: ShiftUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Update shift details."""
    try:
        from app.models.shift import Shift
        old_shift = db.query(Shift).filter(Shift.shift_id == shift_id).first()
        old = snapshot(old_shift) if old_shift else {}
        updated = ShiftService.update_shift(db, shift_id, update_data)
        log_update(db, current_user, "shift", old, updated)
        db.commit()
        return updated
    except SecureTrackException as e:
        handle_service_exception(e)


@router.delete("/shifts/{shift_id}", status_code=200, summary="Delete shift")
def delete_shift(
    shift_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Deactivate a shift."""
    try:
        from app.models.shift import Shift
        shift = db.query(Shift).filter(Shift.shift_id == shift_id).first()
        log_delete(db, current_user, "shift", shift)
        ShiftService.delete_shift(db, shift_id)
        db.commit()
        return {"detail": "Shift deactivated"}
    except SecureTrackException as e:
        handle_service_exception(e)
