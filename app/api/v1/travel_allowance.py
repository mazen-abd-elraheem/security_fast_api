"""
SecureTrack — Travel Allowance API
Auto-generates trip entries from SupervisorVisit data.
Admin/Accountant/CEO can view, edit, and export.
"""
import uuid
import csv
import io
from datetime import datetime, date, timezone, timedelta
from typing import Optional
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.api.deps import require_role, get_current_user
from app.models.travel_allowance_entry import TravelAllowanceEntry
from app.models.supervisor_visit import SupervisorVisit
from app.models.travel_fee import TravelFee
from app.models.site import Site
from app.models.user import User
from app.enums import UserRole

router = APIRouter()


# ── Schemas ──
class TravelAllowanceUpdate(BaseModel):
    amount: Optional[float] = None
    notes: Optional[str] = None
    from_site: Optional[str] = None
    to_site: Optional[str] = None


class TravelAllowanceCreate(BaseModel):
    supervisor_id: str
    trip_date: date
    from_site: str
    to_site: str
    amount: float = 0.0
    notes: Optional[str] = None


# ── Helper: generate next trip number for a date ──
def _next_trip_number(db: Session, trip_date: date) -> str:
    """Generate T-YYYYMMDD-NNN format trip number."""
    prefix = f"T-{trip_date.strftime('%Y%m%d')}-"
    existing = (
        db.query(TravelAllowanceEntry)
        .filter(TravelAllowanceEntry.trip_date == trip_date)
        .count()
    )
    return f"{prefix}{existing + 1:03d}"


# ── Auto-generate entries from supervisor visits ──
def _auto_generate_entries(
    db: Session,
    date_from: date,
    date_to: date,
    supervisor_id: Optional[str] = None,
):
    """
    Scan SupervisorVisit records in the date range and create
    TravelAllowanceEntry rows for trips that don't already exist.
    Uses TravelFee rules for amounts (Base→Site, Site→Site, Site→Base).
    """
    # Fetch travel fee lookup
    travel_fees = db.query(TravelFee).filter(TravelFee.is_active == True).all()
    fee_map = {
        (tf.from_site_name.lower(), tf.to_site_name.lower()): float(tf.amount)
        for tf in travel_fees
    }

    # Get base site
    base_site = db.query(Site).filter(Site.is_base == True).first()
    base_name = base_site.name if base_site else "Base"

    # Fetch supervisors
    sup_filter = [
        User.is_active == True,
        User.role.in_(["supervisor", "leader"]),
    ]
    if supervisor_id:
        sup_filter.append(User.user_id == supervisor_id)
    supervisors = db.query(User).filter(*sup_filter).all()
    sup_ids = [s.user_id for s in supervisors]

    if not sup_ids:
        return

    # Fetch visits in range
    dt_start = datetime.combine(date_from, datetime.min.time())
    dt_end = datetime.combine(date_to, datetime.max.time())

    visits = (
        db.query(SupervisorVisit)
        .options(joinedload(SupervisorVisit.site))
        .filter(
            SupervisorVisit.supervisor_id.in_(sup_ids),
            SupervisorVisit.check_in_time >= dt_start,
            SupervisorVisit.check_in_time <= dt_end,
            SupervisorVisit.is_verified == True,
        )
        .order_by(SupervisorVisit.check_in_time.asc())
        .all()
    )

    # Group visits by (supervisor_id, date)
    visits_by_sup_date = defaultdict(list)
    for v in visits:
        d = v.check_in_time.date()
        visits_by_sup_date[(v.supervisor_id, d)].append(v)

    # Check existing entries to avoid duplicates
    existing_entries = (
        db.query(TravelAllowanceEntry)
        .filter(
            TravelAllowanceEntry.supervisor_id.in_(sup_ids),
            TravelAllowanceEntry.trip_date >= date_from,
            TravelAllowanceEntry.trip_date <= date_to,
            TravelAllowanceEntry.is_active == True,
        )
        .all()
    )
    existing_keys = set()
    for e in existing_entries:
        existing_keys.add((e.supervisor_id, e.trip_date, e.from_site, e.to_site))

    new_entries = []
    for (sid, d), daily_visits in visits_by_sup_date.items():
        if not daily_visits:
            continue

        # Build trip legs: Base → first site
        trips = []
        first_site = daily_visits[0].site.name if daily_visits[0].site else "Unknown"
        trips.append((base_name, first_site))

        # Site-to-site legs
        for i in range(1, len(daily_visits)):
            from_s = daily_visits[i - 1].site.name if daily_visits[i - 1].site else "Unknown"
            to_s = daily_visits[i].site.name if daily_visits[i].site else "Unknown"
            if from_s != to_s:  # Skip same-site visits
                trips.append((from_s, to_s))

        # Last site → Base
        last_site = daily_visits[-1].site.name if daily_visits[-1].site else "Unknown"
        trips.append((last_site, base_name))

        for from_site, to_site in trips:
            key = (sid, d, from_site, to_site)
            if key in existing_keys:
                continue
            existing_keys.add(key)

            amount = fee_map.get((from_site.lower(), to_site.lower()), 0.0)
            trip_num = _next_trip_number(db, d)

            entry = TravelAllowanceEntry(
                entry_id=str(uuid.uuid4()),
                supervisor_id=sid,
                trip_date=d,
                trip_number=trip_num,
                from_site=from_site,
                to_site=to_site,
                amount=amount,
                notes=None,
            )
            new_entries.append(entry)
            db.add(entry)

    if new_entries:
        db.commit()


# ── GET /report ──
@router.get("/report", summary="Travel allowance report grouped by supervisor")
def travel_allowance_report(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    badge_number: Optional[str] = Query(None, description="Search by badge number"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO)),
    db: Session = Depends(get_db),
):
    """
    Auto-generates travel allowance entries from supervisor visits,
    then returns grouped-by-supervisor data with trips and totals.
    """
    # Resolve supervisor_id from badge if provided
    target_sup_id = None
    if badge_number:
        sup = db.query(User).filter(User.badge_number == badge_number).first()
        if not sup:
            return {"supervisors": []}
        target_sup_id = sup.user_id

    # Auto-generate entries from visits
    _auto_generate_entries(db, date_from, date_to, supervisor_id=target_sup_id)

    # Fetch all entries in range
    q = (
        db.query(TravelAllowanceEntry)
        .filter(
            TravelAllowanceEntry.trip_date >= date_from,
            TravelAllowanceEntry.trip_date <= date_to,
            TravelAllowanceEntry.is_active == True,
        )
    )
    if target_sup_id:
        q = q.filter(TravelAllowanceEntry.supervisor_id == target_sup_id)

    entries = q.order_by(TravelAllowanceEntry.trip_date.asc()).all()

    # Get supervisor info
    sup_ids = list(set(e.supervisor_id for e in entries))
    supervisors = db.query(User).filter(User.user_id.in_(sup_ids)).all() if sup_ids else []
    sup_map = {s.user_id: s for s in supervisors}

    # Group entries by supervisor
    grouped = defaultdict(list)
    for e in entries:
        grouped[e.supervisor_id].append(e)

    result = []
    for sid, trips in grouped.items():
        sup = sup_map.get(sid)
        if not sup:
            continue

        total = round(sum(t.amount for t in trips), 2)
        result.append({
            "user_id": sid,
            "name": sup.name,
            "badge_number": sup.badge_number or "",
            "employee_code": sup.employee_code or "",
            "shift_type": sup.shift_type or "",
            "classification": sup.classification or "",
            "total_allowance": total,
            "trips": [
                {
                    "entry_id": t.entry_id,
                    "trip_date": t.trip_date.isoformat(),
                    "trip_number": t.trip_number,
                    "from_site": t.from_site,
                    "to_site": t.to_site,
                    "amount": t.amount,
                    "notes": t.notes or "",
                }
                for t in trips
            ],
        })

    # ── Include ALL active supervisors/leaders even with 0 trips ──
    all_sup_filter = [User.is_active == True, User.role.in_(["supervisor", "leader"])]
    if target_sup_id:
        all_sup_filter.append(User.user_id == target_sup_id)
    all_supervisors = db.query(User).filter(*all_sup_filter).all()

    existing_ids = {r["user_id"] for r in result}
    for sup in all_supervisors:
        if sup.user_id not in existing_ids:
            result.append({
                "user_id": sup.user_id,
                "name": sup.name,
                "badge_number": sup.badge_number or "",
                "employee_code": sup.employee_code or "",
                "shift_type": getattr(sup, 'shift_type', '') or "",
                "classification": sup.classification or "",
                "total_allowance": 0.0,
                "trips": [],
            })

    return {"supervisors": result}



# ── POST / ──
@router.post("/", status_code=201, summary="Create a travel allowance entry")
def create_entry(
    data: TravelAllowanceCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO)),
    db: Session = Depends(get_db),
):
    """Manually create a travel allowance entry."""
    trip_num = _next_trip_number(db, data.trip_date)
    entry = TravelAllowanceEntry(
        entry_id=str(uuid.uuid4()),
        supervisor_id=data.supervisor_id,
        trip_date=data.trip_date,
        trip_number=trip_num,
        from_site=data.from_site,
        to_site=data.to_site,
        amount=data.amount,
        notes=data.notes,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _entry_to_dict(entry)


# ── PUT /{entry_id} ──
@router.put("/{entry_id}", summary="Update a travel allowance entry")
def update_entry(
    entry_id: str,
    data: TravelAllowanceUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO)),
    db: Session = Depends(get_db),
):
    """Edit amount, notes, from_site, to_site of a travel entry."""
    entry = db.query(TravelAllowanceEntry).filter(
        TravelAllowanceEntry.entry_id == entry_id,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    if data.amount is not None:
        entry.amount = data.amount
    if data.notes is not None:
        entry.notes = data.notes
    if data.from_site is not None:
        entry.from_site = data.from_site
    if data.to_site is not None:
        entry.to_site = data.to_site
    entry.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(entry)
    return _entry_to_dict(entry)


# ── DELETE /{entry_id} ──
@router.delete("/{entry_id}", summary="Delete a travel allowance entry")
def delete_entry(
    entry_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Soft-delete a travel allowance entry."""
    entry = db.query(TravelAllowanceEntry).filter(
        TravelAllowanceEntry.entry_id == entry_id,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    entry.is_active = False
    entry.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "Entry deleted"}


# ── GET /export ──
@router.get("/export", summary="Export travel allowance as CSV")
def export_csv(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    badge_number: Optional[str] = Query(None),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO)),
    db: Session = Depends(get_db),
):
    """Export travel allowance report as CSV with Arabic headers."""
    # Auto-generate first
    target_sup_id = None
    if badge_number:
        sup = db.query(User).filter(User.badge_number == badge_number).first()
        if sup:
            target_sup_id = sup.user_id
    _auto_generate_entries(db, date_from, date_to, supervisor_id=target_sup_id)

    # Fetch entries
    q = (
        db.query(TravelAllowanceEntry)
        .filter(
            TravelAllowanceEntry.trip_date >= date_from,
            TravelAllowanceEntry.trip_date <= date_to,
            TravelAllowanceEntry.is_active == True,
        )
    )
    if target_sup_id:
        q = q.filter(TravelAllowanceEntry.supervisor_id == target_sup_id)

    entries = q.order_by(TravelAllowanceEntry.trip_date.asc()).all()

    # Get supervisor info
    sup_ids = list(set(e.supervisor_id for e in entries))
    supervisors = db.query(User).filter(User.user_id.in_(sup_ids)).all() if sup_ids else []
    sup_map = {s.user_id: s for s in supervisors}

    headers = [
        "الاكواد", "مسلسل", "الاسم", "توقيت العمل",
        "التاريخ", "المبلغ", "رقم الرحلة", "من", "الي", "ملاحظه",
    ]

    output = io.StringIO()
    output.write('\ufeff')  # UTF-8 BOM
    writer = csv.writer(output)
    writer.writerow(headers)

    for idx, entry in enumerate(entries, start=1):
        sup = sup_map.get(entry.supervisor_id)
        writer.writerow([
            sup.employee_code or sup.badge_number or "" if sup else "",
            idx,
            sup.name if sup else "Unknown",
            sup.shift_type or "" if sup else "",
            entry.trip_date.isoformat(),
            round(entry.amount, 2),
            entry.trip_number,
            entry.from_site,
            entry.to_site,
            entry.notes or "",
        ])

    # Add totals per supervisor
    writer.writerow([])
    writer.writerow(["--- الإجمالي لكل مشرف ---"])
    for sid in sup_ids:
        sup = sup_map.get(sid)
        if not sup:
            continue
        total = round(sum(e.amount for e in entries if e.supervisor_id == sid), 2)
        writer.writerow([sup.badge_number or "", "", sup.name, "", "", total, "", "", "", ""])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=travel_allowance_{date_from}_to_{date_to}.csv"
        },
    )


def _entry_to_dict(e: TravelAllowanceEntry) -> dict:
    return {
        "entry_id": e.entry_id,
        "supervisor_id": e.supervisor_id,
        "trip_date": e.trip_date.isoformat() if e.trip_date else None,
        "trip_number": e.trip_number,
        "from_site": e.from_site,
        "to_site": e.to_site,
        "amount": e.amount,
        "notes": e.notes or "",
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }
