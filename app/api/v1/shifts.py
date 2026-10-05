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
    Bulk upsert shifts against the EXISTING sites / shifts tables.
    Lookup key: site name + shift label (Arabic-tolerant, case-insensitive).
    - Existing shift (even if soft-deleted) → update only the cells that are
      filled in the sheet and reactivate it. Blank cells never wipe data.
    - Not found → create it on the existing site (same as "Add shift" dialog).
    - Site must already exist — rows for unknown sites are reported, not guessed.
    """
    from app.models.shift import Shift
    from app.services.import_helpers import (
        Lookup, first, parse_time, parse_days, clean, result,
    )
    import uuid as _uuid

    lk = Lookup(db)
    created = updated = skipped = 0
    errors: list[dict] = []

    for idx, row in enumerate(rows, start=1):
        site_name = first(row, "site_name", "Site Name")
        label     = first(row, "label", "shift_label", "Shift Label")
        raw_start = first(row, "start_time", "Start Time", "shift_start")
        raw_end   = first(row, "end_time", "End Time", "shift_end")
        days      = parse_days(first(row, "days_of_week", "Days"))
        raw_head  = clean(row.get("required_headcount") or row.get("Headcount"))

        if not site_name or not label:
            skipped += 1
            errors.append({"row": idx, "reason": "Missing site name or shift label"})
            continue

        site = lk.site(site_name)
        if not site:
            skipped += 1
            errors.append({"row": idx, "reason": f"Site '{site_name}' does not exist — create it first"})
            continue

        start_t = parse_time(raw_start)
        end_t   = parse_time(raw_end)
        if (raw_start and not start_t) or (raw_end and not end_t):
            skipped += 1
            errors.append({"row": idx, "reason": f"Unreadable time '{raw_start or raw_end}' (use HH:MM)"})
            continue

        headcount = None
        if raw_head:
            try:
                headcount = max(1, int(float(raw_head)))
            except ValueError:
                skipped += 1
                errors.append({"row": idx, "reason": f"Headcount '{raw_head}' is not a number"})
                continue

        existing = lk.shift(site.site_id, label)
        if existing:
            changed = False
            if not existing.is_active:
                existing.is_active = True; changed = True
            if start_t and existing.start_time != start_t:
                existing.start_time = start_t; changed = True
            if end_t and existing.end_time != end_t:
                existing.end_time = end_t; changed = True
            if days and existing.days_of_week != days:
                existing.days_of_week = days; changed = True
            if headcount is not None and existing.required_headcount != headcount:
                existing.required_headcount = headcount; changed = True
            if changed:
                db.flush(); updated += 1
            else:
                skipped += 1
        else:
            if not start_t or not end_t:
                skipped += 1
                errors.append({"row": idx, "reason": f"New shift '{label}' needs start and end time"})
                continue
            sh = Shift(
                shift_id=str(_uuid.uuid4()),
                site_id=site.site_id,
                label=label,
                start_time=start_t,
                end_time=end_t,
                days_of_week=days or "mon,tue,wed,thu,fri,sat,sun",
                required_headcount=headcount or 1,
                is_active=True,
            )
            db.add(sh)
            db.flush()
            lk.add_shift(sh)   # later rows in the same file can reference it
            created += 1

    db.commit()
    return result(created, updated, skipped, len(rows), errors, "Shifts import")


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
