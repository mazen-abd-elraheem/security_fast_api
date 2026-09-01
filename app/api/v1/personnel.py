"""
SecureTrack Platform â€” Personnel Officer Routes
Guard site assignments, document uploads, and uniform distribution with condition tracking.
"""
import os
import uuid
from datetime import datetime, timezone, date
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.core.config import get_settings
from app.api.deps import require_role
from app.models.user import User
from app.models.site import Site
from app.models.guard_roster import GuardRoster
from app.models.guard_document import GuardDocument
from app.models.uniform_item import UniformItem
from app.models.inventory_item import InventoryItem
from app.enums import UserRole, DocumentType, UserStatus
from app.core.security import hash_password

router = APIRouter()
settings = get_settings()


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# ════════════════════════════════════════════
# Personnel Officer: Create User (restricted roles)
# ════════════════════════════════════════════

PERSONNEL_ALLOWED_ROLES = {"guard", "lady", "outdoor", "leader", "supervisor"}

class PersonnelCreateUserRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: str = Field(..., min_length=5, max_length=100)
    phone_number: Optional[str] = None
    password: str = Field(..., min_length=6)
    role: str = Field(..., description="guard, lady, outdoor, leader, or supervisor")
    badge_number: Optional[str] = None
    region: Optional[str] = None


@router.post("/create-user", summary="Personnel creates a user (restricted roles)")
def personnel_create_user(
    data: PersonnelCreateUserRequest,
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Personnel officer creates a user account.
    Allowed roles: guard, lady, outdoor, leader, supervisor.
    Cannot create: personnel_officer, operations_manager, admin, ceo, accountant, hr.
    """
    if data.role not in PERSONNEL_ALLOWED_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f"Personnel cannot create role '{data.role}'. Allowed: {', '.join(sorted(PERSONNEL_ALLOWED_ROLES))}"
        )

    # Check duplicate email
    existing = db.query(User).filter(User.email == data.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Check duplicate badge
    if data.badge_number:
        existing_badge = db.query(User).filter(User.badge_number == data.badge_number).first()
        if existing_badge:
            raise HTTPException(status_code=409, detail=f"Badge number already in use: {data.badge_number}")

    import random
    emp_code = str(random.randint(100000, 999999))
    while db.query(User).filter(User.employee_code == emp_code).first():
        emp_code = str(random.randint(100000, 999999))

    db_user = User(
        user_id=str(uuid.uuid4()),
        employee_code=emp_code,
        name=data.name,
        email=data.email,
        phone_number=data.phone_number,
        password_hash=hash_password(data.password),
        role=data.role,
        badge_number=data.badge_number,
        region=data.region,
        is_active=True,
        status=UserStatus.ACTIVE if hasattr(UserStatus, 'ACTIVE') else "active",
    )

    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    return {
        "message": "User created successfully",
        "user_id": db_user.user_id,
        "name": db_user.name,
        "email": db_user.email,
        "role": db_user.role,
        "employee_code": db_user.employee_code,
        "badge_number": db_user.badge_number,
    }\n\n# Guard Site Assignment
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

class AssignGuardRequest(BaseModel):
    guard_id: str
    site_id: str
    shift_id: Optional[str] = None
    date_from: Optional[str] = None  # YYYY-MM-DD
    date_to: Optional[str] = None


@router.post("/assign-guard", summary="Assign guard to site")
def assign_guard(
    data: AssignGuardRequest,
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Assign a guard to a site. Personnel Officer or Admin."""
    guard = db.query(User).filter(User.user_id == data.guard_id, User.role == "guard").first()
    if not guard:
        raise HTTPException(status_code=404, detail="Guard not found")

    site = db.query(Site).filter(Site.site_id == data.site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    # Create roster entry for today if no date specified
    target_date = data.date_from or date.today().isoformat()

    roster = GuardRoster(
        roster_id=str(uuid.uuid4()),
        guard_id=data.guard_id,
        site_id=data.site_id,
        shift_id=data.shift_id,
        date=target_date,
        status="scheduled",
    )
    db.add(roster)
    db.commit()

    return {
        "detail": "Guard assigned to site",
        "roster_id": roster.roster_id,
        "guard_name": guard.name,
        "site_name": site.name,
    }


@router.get("/guards", summary="List all guards with assignments")
def list_guards(
    site_id: Optional[str] = Query(None),
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """List all guards with their current site assignments."""
    guards = db.query(User).filter(User.role == "guard", User.is_active == True).all()

    result = []
    for guard in guards:
        # Get latest roster entry
        latest_roster = db.query(GuardRoster).filter(
            GuardRoster.guard_id == guard.user_id
        ).order_by(GuardRoster.date.desc()).first()

        site_name = None
        current_site_id = None
        if latest_roster:
            site = db.query(Site).filter(Site.site_id == latest_roster.site_id).first()
            site_name = site.name if site else None
            current_site_id = latest_roster.site_id

        if site_id and current_site_id != site_id:
            continue

        # Count documents
        doc_count = db.query(GuardDocument).filter(GuardDocument.guard_id == guard.user_id).count()
        total_doc_types = len(DocumentType)

        result.append({
            "guard_id": guard.user_id,
            "guard_name": guard.name,
            "badge_number": guard.badge_number,
            "phone_number": guard.phone_number,
            "current_site_id": current_site_id,
            "current_site_name": site_name,
            "documents_uploaded": doc_count,
            "documents_required": total_doc_types,
            "documents_complete": doc_count >= total_doc_types,
        })

    return {"guards": result, "total": len(result)}


@router.put("/reassign-guard/{guard_id}", summary="Reassign guard to different site")
def reassign_guard(
    guard_id: str,
    data: AssignGuardRequest,
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Move guard to a different site."""
    guard = db.query(User).filter(User.user_id == guard_id, User.role == "guard").first()
    if not guard:
        raise HTTPException(status_code=404, detail="Guard not found")

    site = db.query(Site).filter(Site.site_id == data.site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    # Cancel existing active roster
    active_rosters = db.query(GuardRoster).filter(
        GuardRoster.guard_id == guard_id,
        GuardRoster.status.in_(["scheduled", "active"]),
    ).all()
    for r in active_rosters:
        r.status = "canceled"

    # Create new assignment
    roster = GuardRoster(
        roster_id=str(uuid.uuid4()),
        guard_id=guard_id,
        site_id=data.site_id,
        shift_id=data.shift_id,
        date=data.date_from or date.today().isoformat(),
        status="scheduled",
    )
    db.add(roster)
    db.commit()

    return {
        "detail": "Guard reassigned",
        "guard_name": guard.name,
        "new_site": site.name,
    }


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Document Management
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@router.post("/documents/{guard_id}", summary="Upload guard document")
async def upload_document(
    guard_id: str,
    document_type: str = Form(...),
    notes: Optional[str] = Form(None),
    file: UploadFile = File(...),
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Upload a document photo for a guard. Personnel Officer or Admin."""
    guard = db.query(User).filter(User.user_id == guard_id).first()
    if not guard:
        raise HTTPException(status_code=404, detail="Guard not found")

    # Validate document type
    valid_types = [dt.value for dt in DocumentType]
    if document_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid document type. Valid: {valid_types}")

    # Check if document of this type already exists â€” replace it
    existing = db.query(GuardDocument).filter(
        GuardDocument.guard_id == guard_id,
        GuardDocument.document_type == document_type,
    ).first()

    # Save file
    doc_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename or "doc.jpg")[1] or ".jpg"
    filename = f"doc_{guard_id}_{document_type}_{doc_id}{ext}"
    filepath = os.path.join(settings.UPLOAD_DIR, filename)

    contents = await file.read()
    with open(filepath, "wb") as f:
        f.write(contents)

    file_url = f"/static/uploads/{filename}"

    if existing:
        # Delete old file
        old_path = os.path.join(settings.UPLOAD_DIR, os.path.basename(existing.file_url))
        if os.path.exists(old_path):
            os.remove(old_path)
        existing.file_url = file_url
        existing.file_name = file.filename
        existing.notes = notes
        existing.uploaded_by = current_user.user_id
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return _doc_response(existing)

    doc = GuardDocument(
        document_id=doc_id,
        guard_id=guard_id,
        document_type=document_type,
        file_url=file_url,
        file_name=file.filename,
        notes=notes,
        uploaded_by=current_user.user_id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return _doc_response(doc)


@router.get("/documents/{guard_id}", summary="Get guard documents")
def get_guard_documents(
    guard_id: str,
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Get all documents for a guard."""
    guard = db.query(User).filter(User.user_id == guard_id).first()
    if not guard:
        raise HTTPException(status_code=404, detail="Guard not found")

    docs = db.query(GuardDocument).filter(GuardDocument.guard_id == guard_id).all()

    # Build status for each document type
    all_types = [dt.value for dt in DocumentType]
    uploaded_types = {d.document_type for d in docs}
    missing_types = [t for t in all_types if t not in uploaded_types]

    return {
        "guard_id": guard_id,
        "guard_name": guard.name,
        "documents": [_doc_response(d) for d in docs],
        "uploaded_count": len(docs),
        "required_count": len(all_types),
        "missing_types": missing_types,
        "is_complete": len(missing_types) == 0,
    }


@router.delete("/documents/{document_id}/delete", summary="Delete document")
def delete_document(
    document_id: str,
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Delete a guard document."""
    doc = db.query(GuardDocument).filter(GuardDocument.document_id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete file
    filepath = os.path.join(settings.UPLOAD_DIR, os.path.basename(doc.file_url))
    if os.path.exists(filepath):
        os.remove(filepath)

    db.delete(doc)
    db.commit()
    return {"detail": "Document deleted"}


@router.get("/documents-status", summary="Documents status dashboard")
def get_documents_status(
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Dashboard: guards with missing required documents."""
    guards = db.query(User).filter(User.role == "guard", User.is_active == True).all()
    all_types = [dt.value for dt in DocumentType]

    incomplete = []
    complete = []
    for guard in guards:
        docs = db.query(GuardDocument).filter(GuardDocument.guard_id == guard.user_id).all()
        uploaded_types = {d.document_type for d in docs}
        missing = [t for t in all_types if t not in uploaded_types]

        entry = {
            "guard_id": guard.user_id,
            "guard_name": guard.name,
            "badge_number": guard.badge_number,
            "uploaded_count": len(docs),
            "required_count": len(all_types),
            "missing_types": missing,
        }

        if missing:
            incomplete.append(entry)
        else:
            complete.append(entry)

    return {
        "total_guards": len(guards),
        "complete_count": len(complete),
        "incomplete_count": len(incomplete),
        "incomplete_guards": incomplete,
    }


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Uniform Distribution (from inventory)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

class IssueUniformRequest(BaseModel):
    guard_id: str
    inventory_item_id: str
    notes: Optional[str] = None


class UpdateConditionRequest(BaseModel):
    condition: str  # new, good, needs_cleaning, damaged, missing
    notes: Optional[str] = None


class ReturnUniformRequest(BaseModel):
    returned_condition: str  # good, damaged, missing, returned_on_termination
    notes: Optional[str] = None


@router.post("/uniform/issue", summary="Issue uniform from inventory")
def issue_uniform(
    data: IssueUniformRequest,
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER, UserRole.ADMIN, UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Issue a uniform item from inventory to a guard. Decrements available stock."""
    guard = db.query(User).filter(User.user_id == data.guard_id).first()
    if not guard:
        raise HTTPException(status_code=404, detail="Guard not found")

    inv_item = db.query(InventoryItem).filter(InventoryItem.item_id == data.inventory_item_id).first()
    if not inv_item:
        raise HTTPException(status_code=404, detail="Inventory item not found")

    if inv_item.quantity_available <= 0:
        raise HTTPException(status_code=400, detail="No stock available for this item")

    # Decrement inventory
    inv_item.quantity_available -= 1

    # Create uniform item
    uniform = UniformItem(
        item_id=str(uuid.uuid4()),
        employee_id=data.guard_id,
        inventory_item_id=data.inventory_item_id,
        item_type=inv_item.item_type,
        size=inv_item.size,
        color=inv_item.color,
        status="issued",
        condition="new",
        notes=data.notes,
        issued_by=current_user.user_id,
    )
    db.add(uniform)
    db.commit()
    db.refresh(uniform)

    return {
        "detail": "Uniform issued",
        "item_id": uniform.item_id,
        "guard_name": guard.name,
        "item_type": uniform.item_type,
        "size": uniform.size,
        "color": uniform.color,
        "remaining_stock": inv_item.quantity_available,
    }


@router.put("/uniform/{item_id}/condition", summary="Update uniform condition")
def update_uniform_condition(
    item_id: str,
    data: UpdateConditionRequest,
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER, UserRole.ADMIN, UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Update uniform condition (needs_cleaning, damaged, etc.)."""
    valid_conditions = ["new", "good", "needs_cleaning", "damaged", "missing"]
    if data.condition not in valid_conditions:
        raise HTTPException(status_code=400, detail=f"Invalid condition. Valid: {valid_conditions}")

    item = db.query(UniformItem).filter(UniformItem.item_id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Uniform item not found")

    item.condition = data.condition
    if data.notes:
        item.notes = data.notes

    # If missing, update status too
    if data.condition == "missing":
        item.status = "lost"
    elif data.condition == "damaged":
        item.status = "damaged"
    elif data.condition == "needs_cleaning":
        item.status = "needs_cleaning"

    db.commit()
    db.refresh(item)
    return _uniform_response(item, db)


@router.put("/uniform/{item_id}/return", summary="Return uniform")
def return_uniform(
    item_id: str,
    data: ReturnUniformRequest,
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER, UserRole.ADMIN, UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Return a uniform item with condition tracking."""
    valid_conditions = ["good", "damaged", "missing", "returned_on_termination"]
    if data.returned_condition not in valid_conditions:
        raise HTTPException(status_code=400, detail=f"Invalid condition. Valid: {valid_conditions}")

    item = db.query(UniformItem).filter(UniformItem.item_id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Uniform item not found")

    item.status = "returned"
    item.condition = data.returned_condition
    item.returned_condition = data.returned_condition
    item.returned_date = datetime.now(timezone.utc)
    if data.notes:
        item.notes = data.notes

    # If returned in good condition, increment inventory back
    if data.returned_condition == "good" and item.inventory_item_id:
        inv = db.query(InventoryItem).filter(InventoryItem.item_id == item.inventory_item_id).first()
        if inv:
            inv.quantity_available += 1

    db.commit()
    db.refresh(item)
    return _uniform_response(item, db)


@router.get("/uniform/guard/{guard_id}", summary="Guard uniform items")
def get_guard_uniforms(
    guard_id: str,
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER, UserRole.ADMIN, UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Get all uniform items for a guard."""
    guard = db.query(User).filter(User.user_id == guard_id).first()
    if not guard:
        raise HTTPException(status_code=404, detail="Guard not found")

    items = db.query(UniformItem).filter(UniformItem.employee_id == guard_id).all()
    return {
        "guard_id": guard_id,
        "guard_name": guard.name,
        "items": [_uniform_response(i, db) for i in items],
        "total": len(items),
        "active": sum(1 for i in items if i.status == "issued"),
    }


@router.get("/uniform/tracker", summary="Uniform tracker")
def get_uniform_tracker(
    status_filter: Optional[str] = Query(None),
    condition_filter: Optional[str] = Query(None),
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Full uniform tracker with condition info."""
    query = db.query(UniformItem)
    if status_filter:
        query = query.filter(UniformItem.status == status_filter)
    if condition_filter:
        query = query.filter(UniformItem.condition == condition_filter)

    items = query.order_by(UniformItem.created_at.desc()).all()

    # Summary
    conditions = {}
    for item in items:
        c = item.condition or "unknown"
        conditions[c] = conditions.get(c, 0) + 1

    return {
        "items": [_uniform_response(i, db) for i in items],
        "total": len(items),
        "by_condition": conditions,
    }


# â”€â”€ Helpers â”€â”€

def _doc_response(doc: GuardDocument) -> dict:
    return {
        "document_id": doc.document_id,
        "guard_id": doc.guard_id,
        "document_type": doc.document_type,
        "file_url": doc.file_url,
        "file_name": doc.file_name,
        "notes": doc.notes,
        "uploaded_by": doc.uploaded_by,
        "expiry_date": doc.expiry_date.isoformat() if doc.expiry_date else None,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


def _uniform_response(item: UniformItem, db: Session) -> dict:
    guard = db.query(User).filter(User.user_id == item.employee_id).first()
    issuer = db.query(User).filter(User.user_id == item.issued_by).first() if item.issued_by else None

    return {
        "item_id": item.item_id,
        "employee_id": item.employee_id,
        "guard_name": guard.name if guard else "Unknown",
        "item_type": item.item_type,
        "size": item.size,
        "color": item.color,
        "status": item.status,
        "condition": item.condition,
        "returned_condition": item.returned_condition,
        "notes": item.notes,
        "issued_by": issuer.name if issuer else None,
        "issued_date": item.issued_date.isoformat() if item.issued_date else None,
        "returned_date": item.returned_date.isoformat() if item.returned_date else None,
        "inventory_item_id": item.inventory_item_id,
    }


# â”€â”€ Document Expiry Alerts â”€â”€

@router.get("/documents/expiring", summary="Get documents expiring soon")
def get_expiring_documents(
    days_ahead: int = Query(30, ge=1, le=90),
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER)),
    db: Session = Depends(get_db),
):
    """Get documents expiring within the next N days."""
    from datetime import date, timedelta
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)

    docs = db.query(GuardDocument).filter(
        GuardDocument.expiry_date != None,
        GuardDocument.expiry_date <= cutoff,
    ).order_by(GuardDocument.expiry_date.asc()).all()

    result = []
    for doc in docs:
        days_left = (doc.expiry_date - today).days
        guard = db.query(User).filter(User.user_id == doc.guard_id).first()
        result.append({
            **_doc_response(doc),
            "expiry_date": doc.expiry_date.isoformat() if doc.expiry_date else None,
            "days_until_expiry": days_left,
            "is_expired": days_left < 0,
            "guard_name": guard.name if guard else "Unknown",
        })
    return {"total": len(result), "documents": result}


# â”€â”€ Guard File Completion % â”€â”€

REQUIRED_DOCUMENTS = [
    "national_id", "military_service", "educational_qualification",
    "criminal_record", "contract", "photo",
]

@router.get("/guards/file-completion", summary="Guard file completion percentage")
def get_file_completion(
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER)),
    db: Session = Depends(get_db),
):
    """Calculate document completion % for each guard."""
    guards = db.query(User).filter(User.role.in_(["guard", "outdoor"])).all()
    result = []
    for guard in guards:
        docs = db.query(GuardDocument).filter(GuardDocument.guard_id == guard.user_id).all()
        doc_types = {d.document_type for d in docs}
        completed = len(doc_types.intersection(set(REQUIRED_DOCUMENTS)))
        total = len(REQUIRED_DOCUMENTS)
        pct = round((completed / total) * 100) if total > 0 else 0
        missing = [d for d in REQUIRED_DOCUMENTS if d not in doc_types]
        result.append({
            "guard_id": guard.user_id,
            "guard_name": guard.name,
            "employee_code": getattr(guard, "employee_code", None) or getattr(guard, "badge_number", None),
            "completed": completed,
            "total_required": total,
            "completion_pct": pct,
            "missing_documents": missing,
        })
    result.sort(key=lambda x: x["completion_pct"])
    return result

