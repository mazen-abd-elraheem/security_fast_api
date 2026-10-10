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
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.PERSONNEL_OFFICER, UserRole.OPERATIONS_MANAGER)),
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
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.OPERATIONS_MANAGER)),
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
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.OPERATIONS_MANAGER)),
    db: Session = Depends(get_db),
):
    """
    Bulk-import roster assignments from Excel/CSV rows (also used by the
    Bulk-Assign wizard).

    Every row is applied EXACTLY like a manual one-by-one assignment:
      - guard resolved by badge (Excel "12.0" → "12"), must be guard/outdoor/lady
      - site resolved against the EXISTING sites table (Arabic-tolerant match)
      - shift resolved against that site's EXISTING shifts (inactive → reactivated)
      - RosterService.upsert_assignment() — same function as POST /roster:
        overlapping bookings for that guard are canceled, same-shift booking
        is updated in place, otherwise a new row is created
      - optional supervisor / leader are linked on the roster row and get a
        route for every day of the range (same as the supervisor assign dialog)
    Rows that can't be applied are reported back with a reason (no silent skips).
    """
    import uuid as _uuid
    from datetime import date as _date, timedelta
    from app.models.supervisor_route import SupervisorRoute
    from app.services.import_helpers import Lookup, first, parse_date, role_of, result

    badges = set()
    for r in rows:
        badges.update({
            first(r, "badge_number", "Badge Number", "guard_badge"),
            first(r, "supervisor_badge", "Supervisor Badge"),
            first(r, "leader_badge", "Leader Badge"),
        })
    lk = Lookup(db, badges)

    created = updated = skipped = 0
    errors: list[dict] = []
    today = _date.today()
    route_jobs: dict[tuple, set] = {}   # (user_id, site_id, shift_id) -> {dates}

    def _skip(idx, reason, badge=""):
        nonlocal skipped
        skipped += 1
        errors.append({"row": idx, "badge": badge, "reason": reason})

    for idx, row in enumerate(rows, start=1):
        badge     = first(row, "badge_number", "Badge Number", "guard_badge")
        site_name = first(row, "site_name", "Site Name")
        shift_lbl = first(row, "shift_label", "Shift Label", "label")
        sup_badge = first(row, "supervisor_badge", "Supervisor Badge")
        ldr_badge = first(row, "leader_badge", "Leader Badge")

        if not badge or not site_name or not shift_lbl:
            _skip(idx, "Missing badge / site / shift", badge); continue

        guard = lk.user(badge)
        if not guard:
            _skip(idx, f"No user with badge '{badge}'", badge); continue
        if role_of(guard) not in RosterService.ASSIGNABLE_ROLES:
            _skip(idx, f"'{guard.name}' is a {role_of(guard)}, not a guard — use the supervisor tab", badge); continue

        site = lk.site(site_name)
        if not site:
            _skip(idx, f"Site '{site_name}' does not exist", badge); continue

        shift = lk.shift(site.site_id, shift_lbl)
        if not shift:
            _skip(idx, f"Shift '{shift_lbl}' does not exist in site '{site.name}'", badge); continue
        if not shift.is_active:
            shift.is_active = True   # assigning to it means it is in use again

        # Dates — same defaults as the manual dialog: start = assigned date
        raw_start = first(row, "start_date", "Start Date")
        raw_end   = first(row, "end_date", "End Date")
        raw_asg   = first(row, "assigned_date", "Date")
        start_d = parse_date(raw_start) or parse_date(raw_asg)
        end_d   = parse_date(raw_end)
        if (raw_start and not parse_date(raw_start)) or (raw_end and not end_d) \
                or (raw_asg and not parse_date(raw_asg) and not start_d):
            _skip(idx, "Unreadable date (use YYYY-MM-DD)", badge); continue
        start_d = start_d or today
        end_d = end_d or start_d

        supervisor = lk.user(sup_badge) if sup_badge else None
        leader     = lk.user(ldr_badge) if ldr_badge else None
        if sup_badge and not supervisor:
            errors.append({"row": idx, "badge": badge, "reason": f"Supervisor badge '{sup_badge}' not found — assigned without supervisor"})
        if ldr_badge and not leader:
            errors.append({"row": idx, "badge": badge, "reason": f"Leader badge '{ldr_badge}' not found — assigned without leader"})

        action, _ = RosterService.upsert_assignment(
            db, guard, shift, start_d, end_d,
            supervisor_id=supervisor.user_id if supervisor else None,
            leader_id=leader.user_id if leader else None,
        )
        if action == "created":
            created += 1
        elif action == "updated":
            updated += 1
        else:
            skipped += 1   # already identical in DB

        lo, hi = min(start_d, end_d), max(start_d, end_d)
        days = {lo + timedelta(days=i) for i in range((hi - lo).days + 1)}
        for staff in (supervisor, leader):
            if staff:
                route_jobs.setdefault((staff.user_id, site.site_id, shift.shift_id), set()).update(days)

    # Supervisor / leader routes — one per day, like the manual "assign supervisor" dialog
    for (uid, sid, shid), days in route_jobs.items():
        existing = {
            r.assigned_date for r in db.query(SupervisorRoute.assigned_date).filter(
                SupervisorRoute.supervisor_id == uid,
                SupervisorRoute.site_id == sid,
                SupervisorRoute.assigned_date.in_(list(days)),
            ).all()
        }
        for d in sorted(days - existing):
            db.add(SupervisorRoute(
                route_id=str(_uuid.uuid4()),
                supervisor_id=uid,
                site_id=sid,
                shift_id=shid,
                assigned_date=d,
                visit_order=1,
                status="pending",
            ))

    db.commit()
    return result(created, updated, skipped, len(rows), errors, "Roster import")


@router.get("/all", summary="Get all roster entries across all sites")
def get_all_roster(
    site_id:   Optional[str]  = Query(None, description="Filter by site"),
    date_from: Optional[date] = Query(None),
    date_to:   Optional[date] = Query(None),
    status:    Optional[str]  = Query(None),
    skip:      int            = Query(0,   ge=0),
    limit:     int            = Query(200, ge=1, le=1000),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPERVISOR, UserRole.OPERATIONS_MANAGER, UserRole.HR, UserRole.PERSONNEL_OFFICER)),
    db: Session = Depends(get_db),
):
    """
    Return ALL guard roster assignments (across all sites) with joined
    guard, shift, site, supervisor, and leader details.
    Supports optional filter by site_id / date range / status.
    """
    from sqlalchemy import func
    from app.models.guard_roster import GuardRoster
    from app.models.shift import Shift
    from app.models.site import Site
    from app.models.user import User as UserModel
    from sqlalchemy.orm import aliased

    Supervisor = aliased(UserModel)
    Leader     = aliased(UserModel)

    subq = (
        db.query(
            GuardRoster.guard_id,
            GuardRoster.shift_id,
            func.max(GuardRoster.assigned_date).label('max_date'),
            func.min(GuardRoster.assigned_date).label('min_date')
        )
        .filter(GuardRoster.status != "canceled")
        .group_by(GuardRoster.guard_id, GuardRoster.shift_id)
        .subquery()
    )

    q = (
        db.query(GuardRoster, Shift, Site, UserModel, Supervisor, Leader, subq.c.min_date, subq.c.max_date)
        .join(subq,
            (GuardRoster.guard_id == subq.c.guard_id) &
            (GuardRoster.shift_id == subq.c.shift_id) &
            (GuardRoster.assigned_date == subq.c.max_date)
        )
        .join(Shift,      GuardRoster.shift_id      == Shift.shift_id)
        .join(Site,       Shift.site_id             == Site.site_id)
        .join(UserModel,  GuardRoster.guard_id      == UserModel.user_id)
        .outerjoin(Supervisor, GuardRoster.supervisor_id == Supervisor.user_id)
        .outerjoin(Leader,     GuardRoster.leader_id     == Leader.user_id)
        .filter(GuardRoster.status != "canceled")
    )

    if site_id:   q = q.filter(Site.site_id         == site_id)
    if date_from: q = q.filter(subq.c.max_date >= date_from)
    if date_to:   q = q.filter(subq.c.min_date <= date_to)
    if status:    q = q.filter(GuardRoster.status       == status)

    total = q.count()
    rows  = q.order_by(GuardRoster.assigned_date.desc(), Site.name, UserModel.name).offset(skip).limit(limit).all()

    items = []
    for roster, shift, site, guard, sup, leader, min_d, max_d in rows:
        items.append({
            "roster_id":       roster.roster_id,
            "guard_id":        guard.user_id,
            "guard_name":      guard.name,
            "guard_badge":     guard.badge_number,
            "site_id":         site.site_id,
            "site_name":       site.name,
            "shift_id":        shift.shift_id,
            "shift_label":     shift.label,
            "shift_start":     str(shift.start_time) if shift.start_time else None,
            "shift_end":       str(shift.end_time)   if shift.end_time   else None,
            "assigned_date":   str(roster.assigned_date),
            "start_date":      str(roster.start_date) if roster.start_date else str(min_d),
            "end_date":        str(roster.end_date)   if roster.end_date   else str(max_d),
            "supervisor_id":   sup.user_id    if sup    else None,
            "supervisor_name": sup.name       if sup    else None,
            "supervisor_badge":sup.badge_number if sup  else None,
            "leader_id":       leader.user_id  if leader else None,
            "leader_name":     leader.name     if leader else None,
            "leader_badge":    leader.badge_number if leader else None,
            "status":          roster.status,
            "created_at":      str(roster.created_at),
        })



    return {"roster": items, "total": total, "skip": skip, "limit": limit}


@router.get("/site/{site_id}", response_model=RosterListResponse, summary="Get roster for site")
def get_roster_for_site(
    site_id: str,
    target_date: date = Query(..., description="Date to get roster for"),
    current_user: User = Depends(require_role(
        UserRole.ADMIN, UserRole.SUPERVISOR, UserRole.OPERATIONS_MANAGER, UserRole.LEADER,
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
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.OPERATIONS_MANAGER)),
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
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPERVISOR, UserRole.OPERATIONS_MANAGER)),
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
