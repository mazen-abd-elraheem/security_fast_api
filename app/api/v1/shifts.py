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


@router.post("/shifts/import-bulk", summary="Bulk import / upsert shifts from CSV")
def import_bulk_shifts(
    rows: list[dict],
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Bulk upsert shifts.
    Lookup key: site_name + label (case-insensitive).
    - Existing shift → smart-update only changed fields.
    - Not found      → create (site must exist by name).
    """
    from app.models.shift import Shift
    from app.models.site import Site
    import uuid as _uuid

    def _ss(v):
        return str(v).strip() if v not in (None, "", "null", "None") else ""

    def _si(v, default=None):
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            return default

    created = updated = skipped = 0

    for row in rows:
        site_name  = _ss(row.get("site_name")  or row.get("Site Name",  ""))
        label      = _ss(row.get("label")       or row.get("Shift Label",""))
        start_time = _ss(row.get("start_time")  or row.get("Start Time", ""))
        end_time   = _ss(row.get("end_time")    or row.get("End Time",   ""))
        days_raw   = _ss(row.get("days_of_week") or row.get("Days", "mon,tue,wed,thu,fri,sat,sun"))
        headcount  = _si(row.get("required_headcount") or row.get("Headcount"), 1)

        if not site_name or not label:
            skipped += 1
            continue

        # Resolve site
        site = db.query(Site).filter(Site.name.ilike(site_name)).first()
        if not site:
            skipped += 1
            continue

        existing = db.query(Shift).filter(
            Shift.site_id == site.site_id,
            Shift.label.ilike(label),
        ).first()

        if existing:
            changed = False
            if start_time and str(existing.start_time or "") != start_time:
                existing.start_time = start_time; changed = True
            if end_time and str(existing.end_time or "") != end_time:
                existing.end_time = end_time; changed = True
            if days_raw and existing.days_of_week != days_raw:
                existing.days_of_week = days_raw; changed = True
            if headcount is not None and existing.required_headcount != headcount:
                existing.required_headcount = headcount; changed = True
            if changed:
                db.flush(); updated += 1
            else:
                skipped += 1
        else:
            if not start_time or not end_time:
                skipped += 1
                continue
            db.add(Shift(
                shift_id=str(_uuid.uuid4()),
                site_id=site.site_id,
                label=label,
                start_time=start_time,
                end_time=end_time,
                days_of_week=days_raw or "mon,tue,wed,thu,fri,sat,sun",
                required_headcount=headcount or 1,
            ))
            db.flush(); created += 1

    db.commit()
    return {
        "detail": f"Shifts import: {created} created, {updated} updated, {skipped} skipped",
        "created_count": created,
        "updated_count": updated,
        "skipped_count": skipped,
        "total_count": len(rows),
    }


@router.get("/{site_id}/shifts", response_model=ShiftListResponse, summary="List shifts for site")
def list_shifts(
    site_id: str,
    current_user: User = Depends(require_role(
        UserRole.ADMIN, UserRole.SUPERVISOR, UserRole.LEADER, 
        UserRole.HR, UserRole.PERSONNEL_OFFICER, 
        UserRole.OPERATIONS_MANAGER, UserRole.CEO
    )),
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
