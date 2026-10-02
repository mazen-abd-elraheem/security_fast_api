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
    Bulk-import roster assignments from CSV rows.
    - Resolves guard by badge_number, shift by (site_name + shift_label)
    - Smart upsert: creates if not exists, updates start/end dates + supervisor/leader if exists
    - Also upserts supervisor route and/or leader route for the date
    """
    from app.models.user import User as UserModel
    from app.models.shift import Shift
    from app.models.site import Site
    from app.models.guard_roster import GuardRoster
    from app.models.supervisor_route import SupervisorRoute
    import uuid as _uuid
    from datetime import date as _date

    def _ss(v):
        return str(v).strip() if v not in (None, "", "null", "None") else ""

    def _parse_date(s):
        if not s: return None
        try:
            from datetime import datetime
            return datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None

    created = updated = skipped = 0
    today = str(_date.today())

    for row in rows:
        badge           = _ss(row.get("badge_number")     or row.get("Badge Number",      ""))
        site_name       = _ss(row.get("site_name")        or row.get("Site Name",          ""))
        shift_lbl       = _ss(row.get("shift_label")      or row.get("Shift Label",        ""))
        date_str        = _ss(row.get("assigned_date")    or row.get("Date", today)) or today
        start_date_str  = _ss(row.get("start_date")       or row.get("Start Date",         ""))
        end_date_str    = _ss(row.get("end_date")         or row.get("End Date",           ""))
        sup_badge       = _ss(row.get("supervisor_badge") or row.get("Supervisor Badge",   ""))
        leader_badge    = _ss(row.get("leader_badge")     or row.get("Leader Badge",       ""))

        if not badge or not site_name or not shift_lbl:
            skipped += 1
            continue

        # Resolve guard
        guard = db.query(UserModel).filter(UserModel.badge_number == badge).first()
        if not guard:
            skipped += 1
            continue

        # Resolve site + shift
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

        # Resolve optional supervisor + leader
        supervisor = db.query(UserModel).filter(UserModel.badge_number == sup_badge).first() if sup_badge else None
        leader     = db.query(UserModel).filter(UserModel.badge_number == leader_badge).first() if leader_badge else None

        start_d = _parse_date(start_date_str)
        end_d   = _parse_date(end_date_str)

        # Smart upsert: find existing non-canceled assignment
        existing_entry = db.query(GuardRoster).filter(
            GuardRoster.guard_id == guard.user_id,
            GuardRoster.shift_id == shift.shift_id,
            GuardRoster.assigned_date == date_str,
        ).first()

        if existing_entry:
            # Update date range + supervisor/leader
            if start_d:   existing_entry.start_date    = start_d
            if end_d:     existing_entry.end_date      = end_d
            if supervisor: existing_entry.supervisor_id = supervisor.user_id
            if leader:     existing_entry.leader_id    = leader.user_id
            db.flush()
            updated += 1
        else:
            db.add(GuardRoster(
                roster_id=str(_uuid.uuid4()),
                guard_id=guard.user_id,
                shift_id=shift.shift_id,
                assigned_date=date_str,
                start_date=start_d,
                end_date=end_d,
                supervisor_id=supervisor.user_id if supervisor else None,
                leader_id=leader.user_id if leader else None,
                status="scheduled",
            ))
            db.flush()
            created += 1

        # Upsert supervisor route for this site+date
        if supervisor:
            sup_route = db.query(SupervisorRoute).filter(
                SupervisorRoute.supervisor_id == supervisor.user_id,
                SupervisorRoute.site_id == site.site_id,
                SupervisorRoute.assigned_date == date_str,
            ).first()
            if not sup_route:
                db.add(SupervisorRoute(
                    route_id=str(_uuid.uuid4()),
                    supervisor_id=supervisor.user_id,
                    site_id=site.site_id,
                    shift_id=shift.shift_id,
                    assigned_date=date_str,
                    visit_order=1,
                    status="pending",
                ))
                db.flush()

        # Upsert leader route if leader is a supervisor-type user
        if leader:
            leader_route = db.query(SupervisorRoute).filter(
                SupervisorRoute.supervisor_id == leader.user_id,
                SupervisorRoute.site_id == site.site_id,
                SupervisorRoute.assigned_date == date_str,
            ).first()
            if not leader_route:
                db.add(SupervisorRoute(
                    route_id=str(_uuid.uuid4()),
                    supervisor_id=leader.user_id,
                    site_id=site.site_id,
                    shift_id=shift.shift_id,
                    assigned_date=date_str,
                    visit_order=1,
                    status="pending",
                ))
                db.flush()

    db.commit()
    return {
        "detail": f"Roster import: {created} created, {updated} updated, {skipped} skipped",
        "created_count": created,
        "updated_count": updated,
        "skipped_count": skipped,
        "total_count": len(rows),
    }


@router.get("/all", summary="Get all roster entries across all sites")
def get_all_roster(
    site_id:   Optional[str]  = Query(None, description="Filter by site"),
    date_from: Optional[date] = Query(None),
    date_to:   Optional[date] = Query(None),
    status:    Optional[str]  = Query(None),
    skip:      int            = Query(0,   ge=0),
    limit:     int            = Query(200, ge=1, le=1000),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPERVISOR)),
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

    # ── Fill empty supervisor / leader from supervisor_routes ──
    from app.models.supervisor_route import SupervisorRoute

    # Collect site_ids that need lookup
    site_ids_needing_sup = {it["site_id"] for it in items if not it["supervisor_id"]}
    site_ids_needing_ldr = {it["site_id"] for it in items if not it["leader_id"]}
    all_site_ids = site_ids_needing_sup | site_ids_needing_ldr

    if all_site_ids:
        # Get latest supervisor_route per site, joined with user to get role
        route_rows = (
            db.query(SupervisorRoute, UserModel)
            .join(UserModel, SupervisorRoute.supervisor_id == UserModel.user_id)
            .filter(SupervisorRoute.site_id.in_(all_site_ids))
            .order_by(SupervisorRoute.assigned_date.desc())
            .all()
        )

        # Build lookup: site_id -> {supervisor: {...}, leader: {...}}
        site_staff = {}  # site_id -> {"supervisor": user, "leader": user}
        for route, user in route_rows:
            sid = route.site_id
            if sid not in site_staff:
                site_staff[sid] = {}
            role = (user.role or "").lower()
            if role == "supervisor" and "supervisor" not in site_staff[sid]:
                site_staff[sid]["supervisor"] = user
            elif role == "leader" and "leader" not in site_staff[sid]:
                site_staff[sid]["leader"] = user

        # Fill missing fields
        for it in items:
            sid = it["site_id"]
            staff = site_staff.get(sid, {})
            if not it["supervisor_id"] and "supervisor" in staff:
                u = staff["supervisor"]
                it["supervisor_id"]    = u.user_id
                it["supervisor_name"]  = u.name
                it["supervisor_badge"] = u.badge_number
            if not it["leader_id"] and "leader" in staff:
                u = staff["leader"]
                it["leader_id"]    = u.user_id
                it["leader_name"]  = u.name
                it["leader_badge"] = u.badge_number

    return {"roster": items, "total": total, "skip": skip, "limit": limit}


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
