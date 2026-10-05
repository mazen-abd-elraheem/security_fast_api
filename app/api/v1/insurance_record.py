import csv
import io
import uuid
from datetime import date, datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
import pandas as pd

from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User
from app.enums import UserRole
from app.models.site import Site
from app.models.guard_roster import GuardRoster
from app.models.supervisor_route import SupervisorRoute
from app.models.insurance_change_request import InsuranceChangeRequest

router = APIRouter(tags=["Insurance Record"])

class InsuranceUpdateItem(BaseModel):
    user_id: str
    field: str
    value: Optional[str | float]

class InsuranceUpdateRequest(BaseModel):
    updates: list[InsuranceUpdateItem]

class InsuranceReviewRequest(BaseModel):
    notes: Optional[str] = None

EDITABLE_FIELDS = {"national_id", "insurance_number", "insurance_status", "insurance_date", "insurable_wage"}


def _current_field_value(user: User, field: str) -> str:
    if field == "insurance_date":
        return user.insurance_date.strftime("%Y-%m-%d") if user.insurance_date else ""
    val = getattr(user, field, None)
    return "" if val is None else str(val)


def _apply_insurance_field(user: User, field: str, value) -> bool:
    """Apply a single editable insurance field to a user. Returns True if applied."""
    if field in ("national_id", "insurance_number", "insurance_status"):
        setattr(user, field, str(value) if value else None)
        return True
    if field == "insurance_date":
        if value:
            try:
                user.insurance_date = datetime.strptime(str(value), "%Y-%m-%d")
                return True
            except ValueError:
                return False
        user.insurance_date = None
        return True
    if field == "insurable_wage":
        try:
            user.insurable_wage = float(value) if value else 0.0
            return True
        except ValueError:
            return False
    return False

def _build_insurance_data(db: Session) -> list[dict]:
    # Get all active guards
    guards = db.query(User).filter(
        User.role == UserRole.GUARD.value,
        User.is_active == True,
        User.status != "terminated"
    ).all()
    
    # Get latest roster for all guards to find site and supervisor
    rosters = db.query(GuardRoster).order_by(GuardRoster.assigned_date.desc()).all()
    
    # Map guard -> site_id
    guard_site_map = {}
    for roster in rosters:
        if roster.guard_id not in guard_site_map:
            guard_site_map[roster.guard_id] = roster.shift.site_id if roster.shift else None
            
    # Fetch all sites
    sites = db.query(Site).all()
    site_map = {s.site_id: s for s in sites}
    
    # Get all supervisors to map user_id -> name
    supervisors = db.query(User).filter(User.role.in_([UserRole.SUPERVISOR.value, UserRole.LEADER.value])).all()
    supervisor_map = {s.user_id: s.name for s in supervisors}
    
    # Map site_id -> supervisor_id based on most recent SupervisorRoute
    routes = db.query(SupervisorRoute).order_by(SupervisorRoute.assigned_date.desc()).all()
    site_supervisor_map = {}
    for r in routes:
        if r.site_id not in site_supervisor_map:
            site_supervisor_map[r.site_id] = r.supervisor_id
    
    results = []
    for guard in guards:
        site_id = guard_site_map.get(guard.user_id)
        site = site_map.get(site_id) if site_id else None
        
        # Site name
        site_name = site.name if site else ""
        
        # Supervisor name (Supervisor routing is many-to-many, take most recent)
        supervisor_name = ""
        if site_id and site_id in site_supervisor_map:
            sup_id = site_supervisor_map[site_id]
            supervisor_name = supervisor_map.get(sup_id, "")
            
        # Role in arabic
        role_ar = "فرد أمن"
        if guard.role == UserRole.SUPERVISOR.value:
            role_ar = "مشرف"
        elif guard.role == UserRole.LEADER.value:
            role_ar = "قائد"
            
        results.append({
            "user_id": guard.user_id,
            "supervisor": supervisor_name,
            "site": site_name,
            "name": guard.name,
            "badge_number": guard.badge_number or "",
            "national_id": guard.national_id or "",
            "hiring_year": str(guard.created_at.year) if guard.created_at else "",
            "hire_date": guard.created_at.strftime("%Y-%m-%d") if guard.created_at else "",
            "insurance_status": guard.insurance_status or "بدون",
            "insurance_number": guard.insurance_number or "",
            "insurance_year": str(guard.insurance_date.year) if guard.insurance_date else "",
            "insurance_date": guard.insurance_date.strftime("%Y-%m-%d") if guard.insurance_date else "",
            "role": role_ar,
            "base_salary": guard.base_salary or 0.0,
            "insurable_wage": guard.insurable_wage or 0.0,
        })
        
    return results

@router.get("/report", summary="Get insurance record data")
def get_insurance_report(
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.CEO, UserRole.ACCOUNTANT, UserRole.HR, UserRole.PERSONNEL_OFFICER, UserRole.OPERATIONS_MANAGER)),
    db: Session = Depends(get_db),
):
    rows = _build_insurance_data(db)

    # Attach pending (unapproved) change requests per employee: {field: new_value}
    pending = db.query(InsuranceChangeRequest).filter(InsuranceChangeRequest.status == "pending").all()
    pending_map: dict[str, dict] = {}
    for p in pending:
        pending_map.setdefault(p.user_id, {})[p.field] = p.new_value
    for r in rows:
        r["pending_changes"] = pending_map.get(r["user_id"], {})

    return {
        "total": len(rows),
        "employees": rows,
    }

@router.put("/update-cells", summary="Batch update editable insurance cells")
def batch_update_insurance_cells(
    data: InsuranceUpdateRequest,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.HR, UserRole.OPERATIONS_MANAGER)),
    db: Session = Depends(get_db),
):
    # NOTE: PERSONNEL_OFFICER is intentionally excluded — Personnel must use
    # /change-requests so HR can approve (four-eyes).
    updated = 0
    for item in data.updates:
        user = db.query(User).filter(User.user_id == item.user_id).first()
        if not user:
            continue
        if _apply_insurance_field(user, item.field, item.value):
            updated += 1
        user.updated_at = datetime.now(timezone.utc)

    db.commit()
    return {"message": f"Updated {updated} cells"}


# ── Four-eyes: Personnel submits → HR approves ──

def _serialize_change_request(r: InsuranceChangeRequest) -> dict:
    return {
        "request_id": r.request_id,
        "user_id": r.user_id,
        "employee_name": r.employee_name,
        "badge_number": r.badge_number,
        "field": r.field,
        "old_value": r.old_value,
        "new_value": r.new_value,
        "status": r.status,
        "requested_by": r.requested_by,
        "requested_by_name": r.requested_by_name,
        "reviewed_by": r.reviewed_by,
        "reviewed_by_name": r.reviewed_by_name,
        "review_notes": r.review_notes,
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.post("/change-requests", summary="Submit insurance cell changes for HR approval")
def submit_change_requests(
    data: InsuranceUpdateRequest,
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    created = 0
    for item in data.updates:
        if item.field not in EDITABLE_FIELDS:
            continue
        user = db.query(User).filter(User.user_id == item.user_id).first()
        if not user:
            continue
        new_value = "" if item.value is None else str(item.value)

        # If a pending request already exists for this employee+field, update it instead of duplicating
        existing = db.query(InsuranceChangeRequest).filter(
            InsuranceChangeRequest.user_id == item.user_id,
            InsuranceChangeRequest.field == item.field,
            InsuranceChangeRequest.status == "pending",
        ).first()
        if existing:
            existing.new_value = new_value
            existing.requested_by = current_user.user_id
            existing.requested_by_name = current_user.name
        else:
            db.add(InsuranceChangeRequest(
                request_id=str(uuid.uuid4()),
                user_id=user.user_id,
                employee_name=user.name,
                badge_number=user.badge_number,
                field=item.field,
                old_value=_current_field_value(user, item.field),
                new_value=new_value,
                status="pending",
                requested_by=current_user.user_id,
                requested_by_name=current_user.name,
            ))
        created += 1

    db.commit()
    return {"message": f"Submitted {created} change(s) for HR approval", "count": created}


@router.get("/change-requests", summary="List insurance change requests")
def list_change_requests(
    status: Optional[str] = Query(None, description="pending / approved / rejected"),
    current_user: User = Depends(require_role(UserRole.HR, UserRole.PERSONNEL_OFFICER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    q = db.query(InsuranceChangeRequest)
    if status:
        q = q.filter(InsuranceChangeRequest.status == status)
    rows = q.order_by(InsuranceChangeRequest.created_at.desc()).all()
    return {"total": len(rows), "requests": [_serialize_change_request(r) for r in rows]}


def _get_reviewable(db: Session, request_id: str, reviewer: User) -> InsuranceChangeRequest:
    req = db.query(InsuranceChangeRequest).filter(InsuranceChangeRequest.request_id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Change request not found")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request already {req.status}")
    if req.requested_by == reviewer.user_id:
        raise HTTPException(status_code=403, detail="Four-eyes rule: you cannot review your own request")
    return req


@router.put("/change-requests/{request_id}/approve", summary="HR approves and applies a change")
def approve_change_request(
    request_id: str,
    data: InsuranceReviewRequest = InsuranceReviewRequest(),
    current_user: User = Depends(require_role(UserRole.HR)),
    db: Session = Depends(get_db),
):
    req = _get_reviewable(db, request_id, current_user)
    user = db.query(User).filter(User.user_id == req.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Employee not found")
    if not _apply_insurance_field(user, req.field, req.new_value):
        raise HTTPException(status_code=400, detail="Invalid value for field")

    now = datetime.now(timezone.utc)
    user.updated_at = now
    req.status = "approved"
    req.reviewed_by = current_user.user_id
    req.reviewed_by_name = current_user.name
    req.review_notes = data.notes
    req.reviewed_at = now
    db.commit()
    return _serialize_change_request(req)


@router.put("/change-requests/{request_id}/reject", summary="HR rejects a change")
def reject_change_request(
    request_id: str,
    data: InsuranceReviewRequest = InsuranceReviewRequest(),
    current_user: User = Depends(require_role(UserRole.HR)),
    db: Session = Depends(get_db),
):
    req = _get_reviewable(db, request_id, current_user)
    req.status = "rejected"
    req.reviewed_by = current_user.user_id
    req.reviewed_by_name = current_user.name
    req.review_notes = data.notes
    req.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    return _serialize_change_request(req)

@router.get("/export-excel", summary="Export insurance record as Excel")
def export_excel(
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.CEO, UserRole.ACCOUNTANT, UserRole.HR, UserRole.PERSONNEL_OFFICER)),
    db: Session = Depends(get_db),
):
    rows = _build_insurance_data(db)
    
    # Define columns exactly as user requested in Arabic
    excel_data = []
    for r in rows:
        excel_data.append({
            "المشرف": r["supervisor"],
            "الفرع": r["site"],
            "الاسم": r["name"],
            "الكود": r["badge_number"],
            "الرقم \nالقومي": r["national_id"],
            "عام \nالتعيين": r["hiring_year"],
            "تاريخ \nالتعيين": r["hire_date"],
            "موقف \nالتأمينات": r["insurance_status"],
            "الرقم \nالتأميني": r["insurance_number"],
            "عام \nالتأمين": r["insurance_year"],
            "تاريخ \nالتأمين \nعليه": r["insurance_date"],
            "الوظيفة": r["role"],
            "أجر \nالاشتراك": r["base_salary"],
            "الأجر \nالشامل": r["insurable_wage"],
        })
        
    df = pd.DataFrame(excel_data)
    
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Insurance Record')
        
    buffer.seek(0)
    
    headers = {
        'Content-Disposition': 'attachment; filename="insurance_record.xlsx"',
        'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    }
    
    return Response(content=buffer.read(), headers=headers)
