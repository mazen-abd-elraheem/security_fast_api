import csv
import io
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

router = APIRouter(tags=["Insurance Record"])

class InsuranceUpdateItem(BaseModel):
    user_id: str
    field: str
    value: Optional[str | float]

class InsuranceUpdateRequest(BaseModel):
    updates: list[InsuranceUpdateItem]

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
    
    results = []
    for guard in guards:
        site_id = guard_site_map.get(guard.user_id)
        site = site_map.get(site_id) if site_id else None
        
        # Site name
        site_name = site.name if site else ""
        
        # Supervisor name
        supervisor_name = ""
        if site and site.manager_id:
            supervisor_name = supervisor_map.get(site.manager_id, "")
            
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
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.CEO, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    rows = _build_insurance_data(db)
    return {
        "total": len(rows),
        "employees": rows,
    }

@router.put("/update-cells", summary="Batch update editable insurance cells")
def batch_update_insurance_cells(
    data: InsuranceUpdateRequest,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    updated = 0
    for item in data.updates:
        user = db.query(User).filter(User.user_id == item.user_id).first()
        if not user:
            continue
            
        # Allowed editable fields
        if item.field in ["national_id", "insurance_number", "insurance_status"]:
            setattr(user, item.field, str(item.value) if item.value else None)
            updated += 1
        elif item.field == "insurance_date":
            if item.value:
                try:
                    dt = datetime.strptime(str(item.value), "%Y-%m-%d")
                    user.insurance_date = dt
                    updated += 1
                except ValueError:
                    pass
            else:
                user.insurance_date = None
                updated += 1
        elif item.field in ["insurable_wage"]:
            try:
                setattr(user, item.field, float(item.value) if item.value else 0.0)
                updated += 1
            except ValueError:
                pass
                
        user.updated_at = datetime.now(timezone.utc)

    db.commit()
    return {"message": f"Updated {updated} cells"}

@router.get("/export-excel", summary="Export insurance record as Excel")
def export_excel(
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.CEO, UserRole.ACCOUNTANT)),
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
