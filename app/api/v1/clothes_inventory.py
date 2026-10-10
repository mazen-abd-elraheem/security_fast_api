from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api import deps
from app.models.clothes_inventory import ClothesRequest, ClothesTermination
from app.models.inventory_item import InventoryItem
from pydantic import BaseModel
from typing import List, Optional, Dict
from datetime import datetime

router = APIRouter()

# Schema for ClothesRequest
class ClothesRequestUpdateItem(BaseModel):
    id: int
    field: str
    value: str | int | float | dict | bool | None

class ClothesTerminationUpdateItem(BaseModel):
    id: int
    field: str
    value: str | int | float | dict | bool | None

class UpdatePayload(BaseModel):
    updates: List[ClothesRequestUpdateItem]

@router.get("/requests")
def get_clothes_requests(db: Session = Depends(deps.get_db), current_user=Depends(deps.get_current_user)):
    records = db.query(ClothesRequest).all()
    if not records:
        return {"records": []}
    
    out = []
    for r in records:
        out.append({
            "id": r.id,
            "user_code": r.user_code,
            "request_date": r.request_date.strftime("%Y-%m-%d") if r.request_date else None,
            "user_name": r.user_name,
            "site_name": r.site_name,
            "reason": r.reason,
            "categories": r.categories or {},
            "received_by": r.received_by,
            "signature": r.signature,
            "status": r.status,
            "ops_manager_id": r.ops_manager_id,
            "ops_reason": r.ops_reason,
            "hr_manager_id": r.hr_manager_id,
            "hr_reason": r.hr_reason,
        })
    return {"records": out}

class CreateRequestPayload(BaseModel):
    user_id: Optional[str] = None
    user_code: Optional[str] = None
    user_name: Optional[str] = None
    site_name: Optional[str] = None
    reason: Optional[str] = None
    items: Dict[str, int] # stock_id -> quantity

@router.post("/requests")
def create_clothes_request(payload: CreateRequestPayload, db: Session = Depends(deps.get_db), current_user=Depends(deps.get_current_user)):
    # 1. Check stock availability and deduct
    for stock_id_str, qty in payload.items.items():
        if qty <= 0: continue
        stock_item = db.query(InventoryItem).filter(InventoryItem.item_id == stock_id_str).first()
        if not stock_item:
            raise HTTPException(status_code=400, detail=f"Stock item {stock_id_str} not found")
        if stock_item.quantity_available < qty:
            raise HTTPException(status_code=400, detail=f"Not enough stock for {stock_item.item_type}. Requested {qty}, available {stock_item.quantity_available}")
        
        # Deduct
        stock_item.quantity_available -= qty
    
    # 2. Create Request
    new_req = ClothesRequest(
        user_id=payload.user_id,
        user_code=payload.user_code,
        user_name=payload.user_name,
        site_name=payload.site_name,
        reason=payload.reason,
        categories=payload.items, # Store the mapped items
        status="pending_ops",
    )
    db.add(new_req)
    db.commit()
    db.refresh(new_req)
    return {"id": new_req.id, "success": True}

class ActionPayload(BaseModel):
    reason: Optional[str] = None

@router.post("/requests/{req_id}/approve")
def approve_request(req_id: int, payload: ActionPayload, db: Session = Depends(deps.get_db), current_user=Depends(deps.get_current_user)):
    req = db.query(ClothesRequest).filter(ClothesRequest.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    
    if req.status == "pending_ops":
        req.status = "pending_hr"
        req.ops_manager_id = current_user.user_id
        if payload.reason:
            req.ops_reason = payload.reason
    elif req.status == "pending_hr":
        req.status = "approved"
        req.hr_manager_id = current_user.user_id
        if payload.reason:
            req.hr_reason = payload.reason
    else:
        raise HTTPException(status_code=400, detail=f"Cannot approve request in status {req.status}")
    
    db.commit()
    return {"success": True, "status": req.status}

@router.post("/requests/{req_id}/reject")
def reject_request(req_id: int, payload: ActionPayload, db: Session = Depends(deps.get_db), current_user=Depends(deps.get_current_user)):
    req = db.query(ClothesRequest).filter(ClothesRequest.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    
    if req.status in ["approved", "rejected"]:
        raise HTTPException(status_code=400, detail=f"Cannot reject request in status {req.status}")
    
    # 1. Restore stock
    items = req.categories or {}
    for stock_id_str, qty in items.items():
        stock_item = db.query(InventoryItem).filter(InventoryItem.item_id == stock_id_str).first()
        if stock_item:
            stock_item.quantity_available += qty

    # 2. Update status and reason
    if req.status == "pending_ops":
        req.ops_manager_id = current_user.user_id
        req.ops_reason = payload.reason
    elif req.status == "pending_hr":
        req.hr_manager_id = current_user.user_id
        req.hr_reason = payload.reason
        
    req.status = "rejected"
    db.commit()
    return {"success": True}

class SendRequestPayload(BaseModel):
    supervisor_id: Optional[str] = None

def sync_clothes_request_to_custody(req: ClothesRequest, db: Session, current_user=None, supervisor_id: Optional[str] = None):
    import uuid
    from datetime import datetime, timezone
    from sqlalchemy import or_
    from app.models.custody_disbursement import CustodyDisbursement
    from app.models.user import User
    from app.models.site import Site
    from app.models.supervisor_route import SupervisorRoute
    from app.models.guard_roster import GuardRoster

    # Check if a disbursement already exists for this request
    existing = db.query(CustodyDisbursement).filter(
        CustodyDisbursement.custody_type == "clothes",
        CustodyDisbursement.ref_id == str(req.id),
        CustodyDisbursement.is_active == True,
    ).first()

    guard = None
    if req.user_id:
        guard = db.query(User).filter(User.user_id == req.user_id).first()
    if not guard and req.user_code:
        guard = db.query(User).filter(
            or_(User.badge_number == req.user_code, User.employee_code == req.user_code)
        ).first()

    guard_id = guard.user_id if guard else (req.user_id or str(uuid.uuid4()))
    guard_name = guard.name if guard else (req.user_name or "حارس")
    guard_badge = guard.badge_number if guard else req.user_code

    site = None
    if req.site_name:
        site = db.query(Site).filter(Site.name == req.site_name).first()
    if not site and guard:
        from app.api.v1.custody import _guard_site
        s_id, s_name = _guard_site(db, guard.user_id)
        if s_id:
            site = db.query(Site).filter(Site.site_id == s_id).first()
    site_id = site.site_id if site else None
    site_name = site.name if site else req.site_name

    sup = None
    if supervisor_id:
        sup = db.query(User).filter(User.user_id == supervisor_id).first()
    if not sup and site_id:
        route = db.query(SupervisorRoute).filter(SupervisorRoute.site_id == site_id).first()
        if route and route.supervisor_id:
            sup = db.query(User).filter(User.user_id == route.supervisor_id).first()
    if not sup and guard:
        roster = db.query(GuardRoster).filter(
            GuardRoster.guard_id == guard.user_id,
            GuardRoster.supervisor_id != None
        ).order_by(GuardRoster.assigned_date.desc()).first()
        if roster and roster.supervisor_id:
            sup = db.query(User).filter(User.user_id == roster.supervisor_id).first()

    supervisor_id_val = sup.user_id if sup else None
    supervisor_name_val = sup.name if sup else None

    # Format description from categories
    item_descs = []
    if isinstance(req.categories, dict):
        for s_id_str, qty in req.categories.items():
            inv = db.query(InventoryItem).filter(InventoryItem.item_id == s_id_str).first()
            if inv:
                lbl = f"{inv.item_type}"
                if inv.size:
                    lbl += f" ({inv.size})"
                if inv.color:
                    lbl += f" {inv.color}"
                item_descs.append(f"{lbl} x{qty}")
            else:
                item_descs.append(f"صنف {s_id_str} x{qty}")
    desc = ", ".join(item_descs) if item_descs else (req.reason or "زي رسمي")
    if req.reason and req.reason not in desc:
        desc += f" — {req.reason}"

    now = datetime.now(timezone.utc)
    if existing:
        if existing.status in ("pending_issue", "pending_hr"):
            existing.status = "pending_receipt"
            existing.issued_at = now
        if supervisor_id_val and not existing.supervisor_id:
            existing.supervisor_id = supervisor_id_val
            existing.supervisor_name = supervisor_name_val
        if site_id and not existing.site_id:
            existing.site_id = site_id
            existing.site_name = site_name
        return existing

    d = CustodyDisbursement(
        disbursement_id=str(uuid.uuid4()),
        custody_type="clothes",
        ref_id=str(req.id),
        guard_id=guard_id,
        guard_name=guard_name,
        guard_badge=guard_badge,
        site_id=site_id,
        site_name=site_name,
        supervisor_id=supervisor_id_val,
        supervisor_name=supervisor_name_val,
        amount=0.0,
        description=desc,
        status="pending_receipt",
        requested_by=req.user_id or (current_user.user_id if current_user else None),
        requested_by_name=current_user.name if current_user else "شؤون العاملين",
        approved_by=req.hr_manager_id,
        approved_at=now,
        issued_at=now,
        is_active=True,
    )
    db.add(d)
    return d

@router.post("/requests/{req_id}/send")
def send_request(
    req_id: int, 
    payload: Optional[SendRequestPayload] = None, 
    db: Session = Depends(deps.get_db), 
    current_user=Depends(deps.get_current_user)
):
    req = db.query(ClothesRequest).filter(ClothesRequest.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != "approved":
        raise HTTPException(status_code=400, detail="Only approved requests can be sent")
    req.status = "sent"
    sup_id = payload.supervisor_id if payload else None
    sync_clothes_request_to_custody(req, db, current_user=current_user, supervisor_id=sup_id)
    db.commit()
    return {"success": True, "status": req.status}


@router.post("/requests/{req_id}/receive")
def receive_request(req_id: int, db: Session = Depends(deps.get_db), current_user=Depends(deps.get_current_user)):
    req = db.query(ClothesRequest).filter(ClothesRequest.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != "sent":
        raise HTTPException(status_code=400, detail="Only sent requests can be taken")
    req.status = "taken"
    db.commit()
    return {"success": True, "status": req.status}

@router.put("/requests/update-cells")
def update_clothes_requests(payload: UpdatePayload, db: Session = Depends(deps.get_db), current_user=Depends(deps.get_current_user)):
    for u in payload.updates:
        rec = db.query(ClothesRequest).filter(ClothesRequest.id == u.id).first()
        if rec:
            if u.field == "categories":
                rec.categories = u.value
            elif u.field == "request_date" and u.value:
                try:
                    rec.request_date = datetime.strptime(u.value, "%Y-%m-%d")
                except:
                    pass
            else:
                setattr(rec, u.field, u.value)
    db.commit()
    return {"success": True}

@router.get("/terminations")
def get_clothes_terminations(db: Session = Depends(deps.get_db), current_user=Depends(deps.get_current_user)):
    records = db.query(ClothesTermination).all()
    out = []
    for r in records:
        out.append({
            "id": r.id,
            "termination_date": r.termination_date.strftime("%Y-%m-%d") if r.termination_date else None,
            "user_name": r.user_name,
            "site_name": r.site_name,
            "supervisor_name": r.supervisor_name,
            "reason": r.reason,
            "clothes_status": r.clothes_status,
            "notes": r.notes,
            "categories": r.categories or {},
            "received_by": r.received_by,
            "signature": r.signature,
        })
    return {"records": out}

@router.put("/terminations/update-cells")
def update_clothes_terminations(payload: UpdatePayload, db: Session = Depends(deps.get_db), current_user=Depends(deps.get_current_user)):
    for u in payload.updates:
        rec = db.query(ClothesTermination).filter(ClothesTermination.id == u.id).first()
        if rec:
            if u.field == "categories":
                rec.categories = u.value
            elif u.field == "termination_date" and u.value:
                try:
                    rec.termination_date = datetime.strptime(u.value, "%Y-%m-%d")
                except:
                    pass
            else:
                setattr(rec, u.field, u.value)
    db.commit()
    db.commit()
    return {"success": True}

class UpdateTerminationStatusPayload(BaseModel):
    clothes_status: str
    received_by: Optional[str] = None
    notes: Optional[str] = None

@router.put("/terminations/{term_id}")
def update_clothes_termination_status(
    term_id: str, 
    payload: UpdateTerminationStatusPayload, 
    db: Session = Depends(deps.get_db), 
    current_user=Depends(deps.get_current_user)
):
    rec = db.query(ClothesTermination).filter(ClothesTermination.user_id == term_id).first()
    if not rec:
        try:
            rec = db.query(ClothesTermination).filter(ClothesTermination.id == int(term_id)).first()
        except ValueError:
            pass
    
    if not rec:
        from app.models.user import User
        user = db.query(User).filter(User.user_id == term_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Termination record not found and user does not exist")
        
        rec = ClothesTermination(
            user_id=term_id,
            user_name=user.name,
            clothes_status=payload.clothes_status,
            received_by=payload.received_by or "HR",
            notes=payload.notes,
        )
        db.add(rec)
        db.commit()
        return {"success": True}
        
    rec.clothes_status = payload.clothes_status
    if payload.received_by is not None:
        rec.received_by = payload.received_by
    if payload.notes is not None:
        rec.notes = payload.notes
        
    db.commit()
    return {"success": True}

class CreateTerminationPayload(BaseModel):
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    site_name: Optional[str] = None
    supervisor_name: Optional[str] = None
    reason: Optional[str] = None
    clothes_status: Optional[str] = None
    notes: Optional[str] = None
    # stock_id -> {"returned": qty, "missing": qty, "destroyed": qty}
    items: Dict[str, Dict[str, int]] 

@router.post("/terminations")
def create_clothes_termination(payload: CreateTerminationPayload, db: Session = Depends(deps.get_db), current_user=Depends(deps.get_current_user)):
    total_deduction = 0.0
    
    # 1. Process items and calculate deductions
    for stock_id_str, status_counts in payload.items.items():
        returned = status_counts.get("returned", 0)
        missing = status_counts.get("missing", 0)
        destroyed = status_counts.get("destroyed", 0)
        
        stock_item = db.query(InventoryItem).filter(InventoryItem.item_id == stock_id_str).first()
        if stock_item:
            # Restore only returned items
            if returned > 0:
                stock_item.quantity_available += returned
            
            # Calculate deduction for missing/destroyed
            cost_per_item = getattr(stock_item, "replacement_cost", 0.0)
            total_deduction += (missing + destroyed) * cost_per_item
            
    # 2. Create Termination Record
    new_term = ClothesTermination(
        user_id=payload.user_id,
        user_name=payload.user_name,
        site_name=payload.site_name,
        supervisor_name=payload.supervisor_name,
        reason=payload.reason,
        clothes_status=payload.clothes_status,
        notes=payload.notes,
        categories=payload.items,
        calculated_deduction=total_deduction,
    )
    db.add(new_term)
    db.commit()
    db.refresh(new_term)
    return {"id": new_term.id, "success": True}
