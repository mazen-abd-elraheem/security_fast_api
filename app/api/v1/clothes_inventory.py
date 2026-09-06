from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api import deps
from app.models.clothes_inventory import ClothesRequest, ClothesTermination
from pydantic import BaseModel
from typing import List, Optional
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
    # If empty, maybe seed a few? Or just return empty
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
        })
    return {"records": out}

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
