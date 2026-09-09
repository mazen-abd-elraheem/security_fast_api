"""
SecureTrack — Employee Documents Sheet API
Endpoint that joins users and their uploaded documents (guard_documents) to track submission status.
"""
import csv
import io
import uuid
from datetime import datetime, timezone
from typing import Optional
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User
from app.models.site import Site
from app.models.shift import Shift
from app.models.guard_roster import GuardRoster
from app.models.guard_document import GuardDocument
from app.models.notification import Notification
from app.enums import UserRole

router = APIRouter()

# Target roles
DOC_ROLES = ["guard", "leader", "supervisor", "lady", "outdoor"]

# Requested Documents
REQUIRED_DOCS = [
    "id_front",
    "id_back",
    "military_service",
    "insurance_print",
    "work_contract",
    "criminal_record"
]

DOC_TRANSLATIONS = {
    "id_front": "الامام",
    "id_back": "الخلف",
    "military_service": "شهاده الخدمه العسكريه",
    "insurance_print": "البرينت التأميني",
    "work_contract": "عقد العمل",
    "criminal_record": "فيش و تشبيه"
}


# ── Schemas ──

class DocBatchUpdateItem(BaseModel):
    user_id: str
    field: str
    value: str  # We'll treat value as string for simplicity (can be 'true', 'false', or text)


class DocBatchUpdateRequest(BaseModel):
    updates: list[DocBatchUpdateItem]


# ── Helper ──

def _build_documents_sheet_data(db: Session) -> list[dict]:
    # 1) Fetch employees
    employees = (
        db.query(User)
        .filter(User.role.in_(DOC_ROLES), User.is_active == True)
        .all()
    )
    employee_ids = [e.user_id for e in employees]

    if not employee_ids:
        return []

    # 2) Fetch active site per employee
    roster_entries = (
        db.query(GuardRoster)
        .filter(GuardRoster.guard_id.in_(employee_ids))
        .all()
    )
    shift_ids = list(set(r.shift_id for r in roster_entries))
    shifts = db.query(Shift).filter(Shift.shift_id.in_(shift_ids)).all() if shift_ids else []
    shift_site_map = {s.shift_id: s.site_id for s in shifts}
    site_ids = list(set(shift_site_map.values()))
    sites = db.query(Site).filter(Site.site_id.in_(site_ids)).all() if site_ids else []
    site_map = {s.site_id: s.name for s in sites}

    emp_site_map: dict[str, str] = {}
    for r in sorted(roster_entries, key=lambda x: x.assigned_date):
        site_id = shift_site_map.get(r.shift_id)
        if site_id:
            emp_site_map[r.guard_id] = site_map.get(site_id, "")

    # 3) Find supervisors per site (Using same logic as Cash Advance Sheet)
    supervisors = (
        db.query(User)
        .filter(User.role.in_(["supervisor", "leader"]), User.is_active == True)
        .all()
    )
    # Simple mapping (in practice, this would use SupervisorRoute)
    # But for this report, we can just use the latest supervisor assigned to the site.
    from app.models.supervisor_route import SupervisorRoute
    sup_routes = db.query(SupervisorRoute).order_by(SupervisorRoute.assigned_date.desc()).all()
    site_sup_map = {}
    for sr in sup_routes:
        if sr.site_id not in site_sup_map:
            for s in supervisors:
                if s.user_id == sr.supervisor_id:
                    site_sup_map[sr.site_id] = s.name
                    break

    emp_supervisor_map = {}
    for eid, sname in emp_site_map.items():
        # reverse lookup site_id from name
        sid = next((k for k, v in site_map.items() if v == sname), None)
        if sid and sid in site_sup_map:
            emp_supervisor_map[eid] = site_sup_map[sid]

    # 4) Fetch documents
    documents = (
        db.query(GuardDocument)
        .filter(GuardDocument.guard_id.in_(employee_ids))
        .all()
    )
    docs_by_guard: dict[str, set[str]] = defaultdict(set)
    for d in documents:
        docs_by_guard[d.guard_id].add(d.document_type)

    # 5) Build rows
    result = []
    for emp in employees:
        eid = emp.user_id
        guard_docs = docs_by_guard[eid]
        
        # Check docs
        doc_status = {
            doc: (doc in guard_docs) for doc in REQUIRED_DOCS
        }
        
        computed_notes = emp.documents_notes
        if not computed_notes:
            missing = [d for d, present in doc_status.items() if not present]
            if missing:
                missing_arabic = [DOC_TRANSLATIONS.get(d, d) for d in missing]
                computed_notes = f"نواقص: {', '.join(missing_arabic)}"
            else:
                computed_notes = "مكتمل"
        
        row = {
            "user_id": eid,
            "role": emp.role.value if hasattr(emp.role, "value") else str(emp.role or ""),
            "supervisor_name": emp_supervisor_map.get(eid, ""),
            "badge_number": emp.badge_number or "",
            "name": emp.name or "",
            "site_name": emp_site_map.get(eid, ""),
            "file_number": emp.file_number or "",
            "id_front": doc_status["id_front"],
            "id_back": doc_status["id_back"],
            "military_service": doc_status["military_service"],
            "insurance_print": doc_status["insurance_print"],
            "work_contract": doc_status["work_contract"],
            "criminal_record": doc_status["criminal_record"],
            "notes": computed_notes,
        }
        result.append(row)

    return result


# ── Endpoints ──

@router.get("/report", summary="Employee documents sheet report")
def get_documents_sheet_report(
    db: Session = Depends(get_db),
    user: User = Depends(require_role([UserRole.ADMIN, UserRole.CEO, UserRole.ACCOUNTANT, UserRole.PERSONNEL_OFFICER]))
):
    """
    Returns the comprehensive documents tracking sheet for all guards/leaders/supervisors.
    """
    data = _build_documents_sheet_data(db)
    return data

@router.get("/photos", summary="Employee documents photos report")
def get_documents_photos_report(
    db: Session = Depends(get_db),
    user: User = Depends(require_role([UserRole.ADMIN, UserRole.CEO, UserRole.ACCOUNTANT, UserRole.PERSONNEL_OFFICER]))
):
    """
    Returns employees with their actual uploaded document photos.
    """
    data = _build_documents_sheet_data(db)
    
    # We also need the actual documents
    employees = (
        db.query(User)
        .filter(User.role.in_(DOC_ROLES), User.is_active == True)
        .all()
    )
    employee_ids = [e.user_id for e in employees]
    documents = (
        db.query(GuardDocument)
        .filter(GuardDocument.guard_id.in_(employee_ids))
        .all()
    )
    
    docs_by_guard = defaultdict(list)
    for d in documents:
        if d.file_url:
            docs_by_guard[d.guard_id].append({
                "type": d.document_type,
                "url": d.file_url
            })
            
    # Filter the data to only include employees with at least one document
    result = []
    for row in data:
        eid = row["user_id"]
        if docs_by_guard[eid]:
            row_copy = row.copy()
            row_copy["documents"] = docs_by_guard[eid]
            result.append(row_copy)
            
    return result


@router.post("/batch-update", summary="Batch update employee documents fields")
def batch_update_cells(
    data: DocBatchUpdateRequest,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    updated = 0
    for item in data.updates:
        user = db.query(User).filter(User.user_id == item.user_id).first()
        if not user:
            continue

        if item.field == "file_number":
            user.file_number = item.value
            updated += 1
        elif item.field == "notes":
            user.documents_notes = item.value
            updated += 1
        elif item.field in REQUIRED_DOCS:
            # item.value is "true" or "false"
            is_checked = item.value.lower() == "true"
            existing_doc = db.query(GuardDocument).filter(
                GuardDocument.guard_id == item.user_id,
                GuardDocument.document_type == item.field
            ).first()

            if is_checked and not existing_doc:
                # Create dummy verified document
                new_doc = GuardDocument(
                    document_id=str(uuid.uuid4()),
                    guard_id=item.user_id,
                    document_type=item.field,
                    file_url="admin_verified",
                    file_name="Admin Verified Override",
                    uploaded_by=current_user.user_id
                )
                db.add(new_doc)
                updated += 1
            elif not is_checked and existing_doc:
                # Delete existing document
                db.delete(existing_doc)
                updated += 1

    db.commit()
    return {"message": f"Updated {updated} cells"}


@router.get("/export-csv", summary="Export documents sheet as CSV")
def export_csv(
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.CEO, UserRole.HR)),
    db: Session = Depends(get_db),
):
    rows = _build_documents_sheet_data(db)

    headers = [
        "مسلسل", "اسم المشرف", "الكود", "الاسم", "الفرع", "رقم الملف",
        "الامام", "الخلف", "شهاده الخدمه العسكريه", "البرينت التأميني",
        "عقد العمل", "فيش و تشبيه", "ملاحظات"
    ]

    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(headers)

    for idx, r in enumerate(rows):
        writer.writerow([
            idx + 1,
            r["supervisor_name"],
            r["badge_number"],
            r["name"],
            r["site_name"],
            r["file_number"],
            "✔️" if r["id_front"] else "❌",
            "✔️" if r["id_back"] else "❌",
            "✔️" if r["military_service"] else "❌",
            "✔️" if r["insurance_print"] else "❌",
            "✔️" if r["work_contract"] else "❌",
            "✔️" if r["criminal_record"] else "❌",
            r["notes"],
        ])

    output.seek(0)
    filename = f"employee_documents_{datetime.now().strftime('%Y-%m-%d')}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

@router.post("/alert/{user_id}", summary="Alert personnel officers about missing documents")
def alert_missing_documents(
    user_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    target_user = db.query(User).filter(User.user_id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    personnel_officers = db.query(User).filter(
        User.role == "personnel_officer",
        User.is_active == True
    ).all()

    if not personnel_officers:
        raise HTTPException(status_code=404, detail="No active personnel officers found")

    alert_message = f"Missing documents for {target_user.role} {target_user.name} (Badge: {target_user.badge_number})"
    
    count = 0
    for po in personnel_officers:
        notif = Notification(
            notification_id=str(uuid.uuid4()),
            user_id=po.user_id,
            notif_type="system",
            title="Missing Documents Alert",
            message=alert_message,
        )
        db.add(notif)
        count += 1
        
    db.commit()
    return {"message": f"Alert sent to {count} personnel officers"}
