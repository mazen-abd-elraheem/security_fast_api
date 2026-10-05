"""
SecureTrack Platform — Supervisor Route Routes
Daily route assignment and itinerary endpoints.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from datetime import date

from app.core.database import get_db
from app.api.deps import get_current_user, require_role, handle_service_exception
from app.models.user import User
from app.enums import UserRole
from app.schemas.route import RouteCreate, BulkRouteCreate, RouteResponse, DailyItineraryResponse
from app.services.route_service import RouteService
from app.core.exceptions import SecureTrackException

router = APIRouter()


@router.post("", status_code=201, summary="Assign daily route")
def assign_route(
    route_data: RouteCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Assign a daily route (list of sites) to a supervisor."""
    try:
        routes = RouteService.assign_route(db, route_data)
        return {"detail": f"{len(routes)} site assignments created", "count": len(routes)}
    except SecureTrackException as e:
        handle_service_exception(e)


@router.post("/bulk", status_code=201, summary="Bulk assign supervisor to date range")
def bulk_assign_route(
    bulk_data: BulkRouteCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Assign a supervisor to the same sites across multiple dates in one call."""
    try:
        routes = RouteService.bulk_assign_route(db, bulk_data.supervisor_id, bulk_data.dates, bulk_data.sites)
        return {"detail": f"{len(routes)} assignments created across {len(bulk_data.dates)} dates", "count": len(routes)}
    except SecureTrackException as e:
        handle_service_exception(e)


@router.post("/import-bulk", status_code=201, summary="Import supervisor/leader routes from CSV rows")
def import_bulk_routes(
    rows: list,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Optimised smart-upsert of supervisor/leader route assignments.

    Improvements over naïve version:
      ① Uses func.lower() == .lower() → hits idx_site_name index (no table scan)
      ② Uses func.lower() == .lower() for shift label lookup → index friendly
      ③ Pre-fetches ALL existing (supervisor_id, site_id, date) keys in ONE
         bulk query per row, eliminating N per-day SELECT round trips
      ④ Bulk-inserts new rows via db.bulk_insert_mappings → single INSERT batch
      ⑤ Batch-updates changed rows in a single pass before final commit
    """
    import uuid as _uuid
    from datetime import date as _date, timedelta, datetime
    from sqlalchemy import func, tuple_
    from app.models.supervisor_route import SupervisorRoute
    from app.models.site import Site
    from app.models.shift import Shift

    from app.services.import_helpers import Lookup, first, parse_date, role_of

    created = updated = skipped = 0
    errors: list[dict] = []
    today = _date.today()

    # ── Phase 1: resolve users, sites, shifts against the live tables ──
    lk = Lookup(db, {first(r, "badge_number", "Badge Number", "supervisor_badge") for r in rows})

    # ── Phase 2: build all (supervisor, site, date) triples ──
    # grouped so we can do one EXISTS query per (supervisor, site) pair
    class _Job:
        __slots__ = ("user_id", "site_id", "shift_id", "dates")
        def __init__(self, uid, sid, shid, dates):
            self.user_id  = uid
            self.site_id  = sid
            self.shift_id = shid
            self.dates    = dates   # list[date]

    jobs: list[_Job] = []

    for idx, row in enumerate(rows, start=1):
        badge       = first(row, "badge_number", "Badge Number", "supervisor_badge")
        site_name   = first(row, "site_name", "Site Name")
        shift_lbl   = first(row, "shift_label", "Shift Label")
        start_d_str = first(row, "start_date", "Start Date")
        end_d_str   = first(row, "end_date", "End Date")
        date_str    = first(row, "assigned_date", "Date")

        if not badge or not site_name:
            skipped += 1
            errors.append({"row": idx, "badge": badge, "reason": "Missing badge or site"})
            continue

        user = lk.user(badge)
        site = lk.site(site_name)
        if not user:
            skipped += 1
            errors.append({"row": idx, "badge": badge, "reason": f"No user with badge '{badge}'"})
            continue
        if role_of(user) not in ("supervisor", "leader", "admin", "operations_manager"):
            skipped += 1
            errors.append({"row": idx, "badge": badge, "reason": f"'{user.name}' is a {role_of(user)} — use the Roster tab for guards"})
            continue
        if not site:
            skipped += 1
            errors.append({"row": idx, "badge": badge, "reason": f"Site '{site_name}' does not exist"})
            continue

        shift_id = None
        if shift_lbl:
            sh = lk.shift(site.site_id, shift_lbl)
            if not sh:
                errors.append({"row": idx, "badge": badge, "reason": f"Shift '{shift_lbl}' not in '{site.name}' — assigned to site without shift"})
            else:
                shift_id = sh.shift_id

        start_d = parse_date(start_d_str)
        end_d   = parse_date(end_d_str)
        single  = parse_date(date_str) or start_d or today

        if start_d and end_d and end_d >= start_d:
            n_days = (end_d - start_d).days + 1
            dates = [start_d + timedelta(days=i) for i in range(n_days)]
        else:
            dates = [single]

        jobs.append(_Job(user.user_id, site.site_id, shift_id, dates))

    # ── Phase 3: batch-check which (sup, site, date) already exist ──
    # Build one query per job to avoid huge cross-product IN clauses
    to_insert: list[dict] = []
    to_update: list[SupervisorRoute] = []

    for job in jobs:
        # ONE query per (supervisor, site) pair — returns all assigned dates
        existing_rows = (
            db.query(SupervisorRoute)
            .filter(
                SupervisorRoute.supervisor_id == job.user_id,
                SupervisorRoute.site_id       == job.site_id,
                SupervisorRoute.assigned_date.in_(job.dates),
            )
            .all()
        )
        existing_map = {r.assigned_date: r for r in existing_rows}

        for d in job.dates:
            if d in existing_map:
                rec = existing_map[d]
                if job.shift_id and rec.shift_id != job.shift_id:
                    rec.shift_id = job.shift_id
                    to_update.append(rec)
                updated += 1
            else:
                to_insert.append({
                    "route_id":     str(_uuid.uuid4()),
                    "supervisor_id": job.user_id,
                    "site_id":       job.site_id,
                    "shift_id":      job.shift_id,
                    "assigned_date": d,
                    "visit_order":   1,
                    "status":        "pending",
                })
                created += 1

    # ── Phase 4: single bulk INSERT + single commit ──
    if to_insert:
        db.bulk_insert_mappings(SupervisorRoute, to_insert)
    # Updated rows are already tracked by SQLAlchemy; just commit
    db.commit()

    return {
        "detail": f"Route import: {created} created, {updated} updated, {skipped} skipped",
        "created_count": created,
        "updated_count": updated,
        "skipped_count": skipped,
        "total_count": len(rows),
        "errors": errors[:200],
    }



def _build_itinerary(routes, supervisor_id, supervisor_name, target_date):
    """Helper to build DailyItineraryResponse."""
    route_items = []
    for r in routes:
        route_items.append(RouteResponse(
            route_id=r.route_id,
            supervisor_id=r.supervisor_id,
            supervisor_name=supervisor_name,
            site_id=r.site_id,
            site_name=r.site.name if r.site else None,
            site_address=r.site.address if r.site else None,
            assigned_date=r.assigned_date,
            visit_order=r.visit_order,
            status=r.status,
            created_at=r.created_at,
        ))
    completed = sum(1 for r in routes if r.status == "completed")
    total = len(routes)
    return DailyItineraryResponse(
        supervisor_id=supervisor_id,
        supervisor_name=supervisor_name,
        assigned_date=target_date,
        routes=route_items,
        total_sites=total,
        completed_sites=completed,
        progress_percentage=round(completed / total * 100, 1) if total > 0 else 0.0,
    )


@router.get("/my", response_model=DailyItineraryResponse, summary="Get my today's route")
def get_my_route(
    target_date: date = Query(default=None, description="Date (defaults to today)"),
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.OPERATIONS_MANAGER, UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    """Get the authenticated supervisor's daily route."""
    if not target_date:
        target_date = date.today()
    routes = RouteService.get_daily_route(db, current_user.user_id, target_date)
    return _build_itinerary(routes, current_user.user_id, current_user.name, target_date)


@router.get("/supervisor/{supervisor_id}", response_model=DailyItineraryResponse, summary="Get supervisor's route")
def get_supervisor_route(
    supervisor_id: str,
    target_date: date = Query(default=None),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Get a specific supervisor's daily route."""
    if not target_date:
        target_date = date.today()
    routes = RouteService.get_daily_route(db, supervisor_id, target_date)
    supervisor = db.query(User).filter(User.user_id == supervisor_id).first()
    name = supervisor.name if supervisor else "Unknown"
    return _build_itinerary(routes, supervisor_id, name, target_date)


@router.get("/date/{target_date}", summary="Get all routes for a date")
def get_routes_for_date(
    target_date: date,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Get all supervisor routes for a specific date."""
    routes = RouteService.get_all_routes_for_date(db, target_date)

    # Batch-load supervisors and sites to avoid N+1 lazy queries
    sup_ids = {r.supervisor_id for r in routes}
    site_ids = {r.site_id for r in routes}
    sup_map = {}
    site_map_data = {}
    if sup_ids:
        sups = db.query(User).filter(User.user_id.in_(sup_ids)).all()
        sup_map = {s.user_id: s for s in sups}
    if site_ids:
        from app.models.site import Site
        sites_q = db.query(Site).filter(Site.site_id.in_(site_ids)).all()
        site_map_data = {s.site_id: s for s in sites_q}

    items = []
    for r in routes:
        sup = sup_map.get(r.supervisor_id)
        site_obj = site_map_data.get(r.site_id)
        items.append(RouteResponse(
            route_id=r.route_id,
            supervisor_id=r.supervisor_id,
            supervisor_name=sup.name if sup else None,
            supervisor_role=(sup.role.value if hasattr(sup.role, 'value') else sup.role) if sup and sup.role else None,
            site_id=r.site_id,
            shift_id=r.shift_id,
            site_name=site_obj.name if site_obj else None,
            site_address=site_obj.address if site_obj else None,
            assigned_date=r.assigned_date,
            visit_order=r.visit_order,
            status=r.status,
            created_at=r.created_at,
        ))
    return {"routes": items, "total": len(items), "date": target_date.isoformat()}



@router.put("/{route_id}", response_model=RouteResponse, summary="Update route status")
def update_route_status(
    route_id: str,
    status: str = Query(..., description="New status: pending, in_progress, completed, skipped"),
    current_user: User = Depends(require_role(UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Update a route assignment's status."""
    try:
        route = RouteService.update_route_status(db, route_id, status)
        return RouteResponse(
            route_id=route.route_id,
            supervisor_id=route.supervisor_id,
            site_id=route.site_id,
            site_name=route.site.name if route.site else None,
            site_address=route.site.address if route.site else None,
            assigned_date=route.assigned_date,
            visit_order=route.visit_order,
            status=route.status,
            created_at=route.created_at,
        )
    except SecureTrackException as e:
        handle_service_exception(e)


@router.get("/user/{user_id}/assignments", summary="Get assignment summary for a user (conflict check)")
def get_user_assignments(
    user_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Returns a lightweight summary of route assignments grouped by site,
    used for conflict detection UI. No date range filter — returns all.
    """
    from app.models.supervisor_route import SupervisorRoute
    from app.models.site import Site
    from sqlalchemy import func

    # One query: GROUP BY site_id → count + date range
    rows = (
        db.query(
            SupervisorRoute.site_id,
            func.count(SupervisorRoute.route_id).label("count"),
            func.min(SupervisorRoute.assigned_date).label("date_from"),
            func.max(SupervisorRoute.assigned_date).label("date_to"),
        )
        .filter(SupervisorRoute.supervisor_id == user_id)
        .group_by(SupervisorRoute.site_id)
        .all()
    )

    total = sum(r.count for r in rows)

    # Batch-load site names
    site_ids = [r.site_id for r in rows]
    site_map = {}
    if site_ids:
        sites = db.query(Site).filter(Site.site_id.in_(site_ids)).all()
        site_map = {s.site_id: s.name for s in sites}

    summary = []
    for r in rows:
        summary.append({
            "site_id": r.site_id,
            "site_name": site_map.get(r.site_id, "Unknown"),
            "count": r.count,
            "date_from": r.date_from.isoformat() if r.date_from else None,
            "date_to": r.date_to.isoformat() if r.date_to else None,
        })

    return {"sites": summary, "total": total, "user_id": user_id}


@router.get("/all", summary="Get all supervisor/leader route assignments")
def get_all_routes(
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Return all supervisor_routes grouped by user+site, with date range,
    for the admin roster management UI (display, export, re-import).
    """
    from app.models.supervisor_route import SupervisorRoute
    from app.models.site import Site
    from app.models.shift import Shift
    from sqlalchemy import func

    # Deduplicate by user + site → take min/max date
    subq = (
        db.query(
            SupervisorRoute.supervisor_id,
            SupervisorRoute.site_id,
            func.min(SupervisorRoute.assigned_date).label("date_from"),
            func.max(SupervisorRoute.assigned_date).label("date_to"),
            func.count(SupervisorRoute.route_id).label("count"),
        )
        .group_by(SupervisorRoute.supervisor_id, SupervisorRoute.site_id)
        .subquery()
    )

    # Join the representative route row (the latest one per user+site)
    rep_route = (
        db.query(SupervisorRoute)
        .join(subq,
            (SupervisorRoute.supervisor_id == subq.c.supervisor_id) &
            (SupervisorRoute.site_id == subq.c.site_id) &
            (SupervisorRoute.assigned_date == subq.c.date_to)
        )
        .all()
    )

    # Batch load users and sites
    user_ids = {r.supervisor_id for r in rep_route}
    site_ids = {r.site_id for r in rep_route}
    shift_ids = {r.shift_id for r in rep_route if r.shift_id}

    user_map = {}
    site_map = {}
    shift_map = {}
    if user_ids:
        users = db.query(User).filter(User.user_id.in_(user_ids)).all()
        user_map = {u.user_id: u for u in users}
    if site_ids:
        sites = db.query(Site).filter(Site.site_id.in_(site_ids)).all()
        site_map = {s.site_id: s for s in sites}
    if shift_ids:
        shifts = db.query(Shift).filter(Shift.shift_id.in_(shift_ids)).all()
        shift_map = {sh.shift_id: sh for sh in shifts}

    # Also fetch date range from subq
    date_range = {
        (row.supervisor_id, row.site_id): (row.date_from, row.date_to)
        for row in db.query(subq).all()
    }

    items = []
    for r in rep_route:
        u = user_map.get(r.supervisor_id)
        s = site_map.get(r.site_id)
        sh = shift_map.get(r.shift_id) if r.shift_id else None
        d_from, d_to = date_range.get((r.supervisor_id, r.site_id), (None, None))
        items.append({
            "route_id":        r.route_id,
            "supervisor_id":   r.supervisor_id,
            "supervisor_name": u.name         if u  else None,
            "supervisor_badge":u.badge_number if u  else None,
            "supervisor_role": (u.role.value if hasattr(u.role, 'value') else u.role) if u and u.role else None,
            "site_id":         r.site_id,
            "site_name":       s.name         if s  else None,
            "shift_id":        r.shift_id,
            "shift_label":     sh.label       if sh else None,
            "start_date":      d_from.isoformat() if d_from else None,
            "end_date":        d_to.isoformat()   if d_to   else None,
            "assigned_date":   r.assigned_date.isoformat(),
            "status":          r.status,
        })

    items.sort(key=lambda x: (x.get("supervisor_name") or "", x.get("site_name") or ""))
    return {"routes": items[skip: skip + limit], "total": len(items)}
