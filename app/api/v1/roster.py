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


@router.post("/import-bulk", status_code=201, summary="Import roster assignments from CSV")
def import_bulk_roster(
    rows: list[dict],
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Bulk-create roster assignments from CSV rows.
    Resolves guard by badge_number, shift by (site_name + shift_label).
    Skips duplicates (guard already assigned to same shift on same date).
    """
    from app.models.user import User as UserModel
    from app.models.shift import Shift
    from app.models.site import Site
    from app.models.guard_roster import GuardRoster
    import uuid as _uuid
    from datetime import date as _date

    def _ss(v):
        return str(v).strip() if v not in (None, "", "null", "None") else ""

    created = skipped = 0
    today = str(_date.today())

    for row in rows:
        badge      = _ss(row.get("badge_number") or row.get("Badge Number", ""))
        site_name  = _ss(row.get("site_name")    or row.get("Site Name",    ""))
        shift_lbl  = _ss(row.get("shift_label")  or row.get("Shift Label",  ""))
        date_str   = _ss(row.get("assigned_date") or row.get("Date", today))
        if not date_str:
            date_str = today

        if not badge or not site_name or not shift_lbl:
            skipped += 1
            continue

        # Resolve guard
        guard = db.query(UserModel).filter(UserModel.badge_number == badge).first()
        if not guard:
            skipped += 1
            continue

        # Resolve site → shift
        site = db.query(Site).filter(Site.name.ilike(site_name)).first()
        if not site:
            skipped += 1
            continue

        shift = db.query(Shift).filter(
            Shift.site_id == site.site_id,
            Shift.label.ilike(shift_lbl),
        ).first()
        if not shift:
            skipped += 1
            continue

        # Check duplicate
        dup = db.query(GuardRoster).filter(
            GuardRoster.guard_id == guard.user_id,
            GuardRoster.shift_id == shift.shift_id,
            GuardRoster.assigned_date == date_str,
            GuardRoster.status != "canceled",
        ).first()
        if dup:
            skipped += 1
            continue

        db.add(GuardRoster(
            roster_id=str(_uuid.uuid4()),
            guard_id=guard.user_id,
            shift_id=shift.shift_id,
            assigned_date=date_str,
            status="scheduled",
        ))
        db.flush()
        created += 1

    db.commit()
    return {
        "detail": f"Roster import: {created} created, {skipped} skipped",
        "created_count": created,
        "skipped_count": skipped,
        "total_count": len(rows),
    }


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
        from app.core.audit import log_audit, log_create, log_update, log_delete, log_read, snapshot
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


@router.get("/guard-conflicts", summary="Check if guard has conflicting assignments in a date range")
def get_guard_conflicts(
    guard_id: str = Query(...),
    date_from: date = Query(...),
    date_to: date = Query(...),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """
    Returns all active roster assignments for this guard in the given date range.
    Used to warn before reassigning a guard who's already scheduled somewhere.
    """
    from app.models.guard_roster import GuardRoster
    from app.models.shift import Shift
    from app.models.site import Site
    from collections import defaultdict

    rows = (
        db.query(GuardRoster, Shift, Site)
        .join(Shift, GuardRoster.shift_id == Shift.shift_id)
        .join(Site, Shift.site_id == Site.site_id)
        .filter(
            GuardRoster.guard_id == guard_id,
            GuardRoster.assigned_date >= date_from,
            GuardRoster.assigned_date <= date_to,
            GuardRoster.status != "canceled",
        )
        .order_by(GuardRoster.assigned_date.asc())
        .all()
    )

    if not rows:
        return {"conflicts": [], "total_conflict_days": 0}

    # Group conflicts by (site, shift) and list the affected dates
    groups: dict = defaultdict(list)
    for roster, shift, site in rows:
        key = (site.name, shift.label or "", str(shift.start_time or ""), str(shift.end_time or ""))
        groups[key].append(str(roster.assigned_date))

    conflicts = [
        {
            "site_name": k[0],
            "shift_label": k[1],
            "shift_time": f"{k[2][:5]} - {k[3][:5]}" if k[2] and k[3] else "",
            "days": v,
            "total_days": len(v),
        }
        for k, v in groups.items()
    ]

    return {"conflicts": conflicts, "total_conflict_days": len(rows)}
