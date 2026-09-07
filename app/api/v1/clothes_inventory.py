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
