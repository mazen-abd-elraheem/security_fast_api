"""
SecureTrack — Bonus Sheet API
Dedicated endpoint that manages bonus requests, mirroring the cash advance sheet.
"""
import csv
import io
import uuid
from datetime import date, datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_

from app.core.database import get_db
from app.api.deps import require_role, get_current_user
from app.models.user import User
from app.models.bonus import Bonus
from app.schemas.bonus_schemas import BonusCreate, BonusUpdate, BonusOut
from app.enums import UserRole

router = APIRouter()

@router.get("/eligible-users", summary="Get users eligible for bonuses")
def get_eligible_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.CEO, UserRole.HR, UserRole.OPERATIONS_MANAGER))
):
    """Get active users in field roles eligible for a bonus."""
    eligible_roles = ["guard", "lady", "supervisor", "outdoor"]
    users = db.query(User).filter(
        User.role.in_(eligible_roles),
        User.is_active == True
    ).all()
    
    return [
        {
            "guard_id": u.user_id,
            "guard_name": u.name,
            "badge_number": u.badge_number,
            "role": u.role,
        }
        for u in users
    ]

@router.post("/", response_model=BonusOut, summary="Create a new bonus request")
def create_bonus(
    payload: BonusCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.CEO, UserRole.HR, UserRole.OPERATIONS_MANAGER))
):
    guard = db.query(User).filter(User.user_id == payload.guard_id).first()
    if not guard:
        raise HTTPException(status_code=404, detail="Guard not found")

    new_bonus = Bonus(
        bonus_id=str(uuid.uuid4()),
        guard_id=payload.guard_id,
        guard_name=payload.guard_name or guard.name,
        guard_code=payload.guard_code or guard.badge_number,
        site_id=payload.site_id,
        site_name=payload.site_name,
        supervisor_name=payload.supervisor_name,
        shift_time=payload.shift_time,
        amount=payload.amount,
        photo_url=payload.photo_url,
        notes=payload.notes,
        status="pending",
        created_at=payload.date if payload.date else func.now()
    )

    db.add(new_bonus)
    db.commit()
    db.refresh(new_bonus)
    return new_bonus


@router.get("/", response_model=List[BonusOut], summary="Get bonus requests by date and status")
def get_bonuses(
    date_from: date,
    date_to: date,
    tab: str = Query(..., description="pending, approved, or rejected"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.CEO, UserRole.HR, UserRole.ACCOUNTANT))
):
    status_filter = []
    if tab == "approved":
        status_filter = ["approved"]
    elif tab == "rejected":
        status_filter = ["rejected"]
    else:
        status_filter = ["pending"]

    query = (
        db.query(Bonus)
        .filter(
            Bonus.status.in_(status_filter),
            Bonus.created_at >= datetime.combine(date_from, datetime.min.time()),
            Bonus.created_at <= datetime.combine(date_to, datetime.max.time()),
        )
    )

    bonuses = query.order_by(Bonus.created_at.desc()).all()
    return bonuses


@router.put("/{bonus_id}/status", response_model=BonusOut, summary="Update bonus status (approve/reject)")
def update_bonus_status(
    bonus_id: str,
    payload: BonusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.CEO, UserRole.ACCOUNTANT))
):
    bonus = db.query(Bonus).filter(Bonus.bonus_id == bonus_id).first()
    if not bonus:
        raise HTTPException(status_code=404, detail="Bonus not found")

    if payload.status:
        if payload.status not in ["pending", "approved", "rejected"]:
            raise HTTPException(status_code=400, detail="Invalid status")
        bonus.status = payload.status
        if payload.status == "approved":
            bonus.approved_at = datetime.now(timezone.utc)
            bonus.approved_by = current_user.user_id

    if payload.amount is not None:
        bonus.amount = payload.amount

    if payload.notes is not None:
        bonus.notes = payload.notes

    db.commit()
    db.refresh(bonus)
    return bonus


@router.get("/export/csv", summary="Export bonuses to CSV")
def export_bonuses_csv(
    date_from: date,
    date_to: date,
    tab: str = Query("approved"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.CEO, UserRole.HR, UserRole.ACCOUNTANT))
):
    status_filter = ["approved"] if tab == "approved" else (["rejected"] if tab == "rejected" else ["pending"])

    bonuses = (
        db.query(Bonus)
        .filter(
            Bonus.status.in_(status_filter),
            Bonus.created_at >= datetime.combine(date_from, datetime.min.time()),
            Bonus.created_at <= datetime.combine(date_to, datetime.max.time()),
        )
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Code", "Name", "Site", "Supervisor", "Shift", "Amount", "Status", "Notes", "Created At"
    ])

    for b in bonuses:
        writer.writerow([
            b.guard_code or "",
            b.guard_name or "",
            b.site_name or "",
            b.supervisor_name or "",
            b.shift_time or "",
            f"{b.amount:.2f}",
            b.status,
            b.notes or "",
            b.created_at.strftime("%Y-%m-%d %H:%M:%S") if b.created_at else ""
        ])

    output.seek(0)
    response = StreamingResponse(iter([output.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=bonuses_{date_from}_to_{date_to}.csv"
    return response
