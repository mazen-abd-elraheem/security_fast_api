"""
SecureTrack Platform â€” Inventory Routes
Admin manages clothing/uniform stock. Personnel Officer + Admin can view.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User
from app.models.inventory_item import InventoryItem
from app.enums import UserRole
from app.core.audit import log_audit, log_create, log_update, log_delete, log_read, snapshot

router = APIRouter()


# â”€â”€ Schemas â”€â”€

class InventoryCreate(BaseModel):
    item_type: str = Field(..., max_length=30)
    size: Optional[str] = Field(None, max_length=20)
    color: Optional[str] = Field(None, max_length=50)
    quantity: int = Field(..., ge=1)
    min_stock_level: int = Field(5, ge=0)
    notes: Optional[str] = None


class InventoryUpdate(BaseModel):
    add_quantity: Optional[int] = Field(None, ge=0)
    min_stock_level: Optional[int] = Field(None, ge=0)
    notes: Optional[str] = None
    color: Optional[str] = None
    size: Optional[str] = None


# â”€â”€ Add new stock â”€â”€
@router.post("", status_code=201, summary="Add inventory stock")
def add_inventory(
    data: InventoryCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Add new clothing stock to inventory. Admin only."""
    # Check if item with same type+size+color already exists
    existing = db.query(InventoryItem).filter(
        InventoryItem.item_type == data.item_type,
        InventoryItem.size == data.size,
        InventoryItem.color == data.color,
    ).first()

    if existing:
        existing.quantity_total += data.quantity
        existing.quantity_available += data.quantity
        if data.notes:
            existing.notes = data.notes
        existing.min_stock_level = data.min_stock_level
        db.commit()
        db.refresh(existing)
        return _to_response(existing)

    item = InventoryItem(
        item_id=str(uuid.uuid4()),
        item_type=data.item_type,
        size=data.size,
        color=data.color,
        quantity_total=data.quantity,
        quantity_available=data.quantity,
        min_stock_level=data.min_stock_level,
        notes=data.notes,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _to_response(item)


# â”€â”€ List all inventory â”€â”€
@router.get("", summary="List inventory items")
def list_inventory(
    item_type: Optional[str] = Query(None),
    low_stock_only: bool = Query(False),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.PERSONNEL_OFFICER)),
    db: Session = Depends(get_db),
):
    """List all inventory items. Admin + Personnel Officer."""
    query = db.query(InventoryItem)
    if item_type:
        query = query.filter(InventoryItem.item_type == item_type)
    if low_stock_only:
        query = query.filter(InventoryItem.quantity_available <= InventoryItem.min_stock_level)
    items = query.order_by(InventoryItem.item_type, InventoryItem.size).all()
    return {
        "items": [_to_response(i) for i in items],
        "total": len(items),
    }


# â”€â”€ Get low stock alerts â”€â”€
@router.get("/low-stock", summary="Low stock alerts")
def get_low_stock(
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.PERSONNEL_OFFICER)),
    db: Session = Depends(get_db),
):
    """Get inventory items below minimum stock level."""
    items = db.query(InventoryItem).filter(
        InventoryItem.quantity_available <= InventoryItem.min_stock_level
    ).all()
    return {
        "low_stock_items": [_to_response(i) for i in items],
        "total": len(items),
    }


# â”€â”€ Summary dashboard â”€â”€
@router.get("/summary", summary="Inventory summary")
def get_inventory_summary(
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.PERSONNEL_OFFICER)),
    db: Session = Depends(get_db),
):
    """Get inventory summary stats."""
    all_items = db.query(InventoryItem).all()
    total_types = len(all_items)
    total_stock = sum(i.quantity_available for i in all_items)
    low_stock = sum(1 for i in all_items if i.quantity_available <= i.min_stock_level)

    by_type = {}
    size_breakdown = {}
    for item in all_items:
        if item.item_type not in by_type:
            by_type[item.item_type] = {"total_available": 0, "variants": 0}
        by_type[item.item_type]["total_available"] += item.quantity_available
        by_type[item.item_type]["variants"] += 1

        if item.item_type not in size_breakdown:
            size_breakdown[item.item_type] = []
        size_breakdown[item.item_type].append({
            "item_id": item.item_id,
            "size": item.size,
            "color": item.color,
            "quantity_available": item.quantity_available,
            "quantity_total": item.quantity_total,
            "min_stock_level": item.min_stock_level,
            "is_low_stock": item.quantity_available <= item.min_stock_level,
        })

    return {
        "total_item_types": total_types,
        "total_available_stock": total_stock,
        "low_stock_count": low_stock,
        "by_type": by_type,
        "size_breakdown": size_breakdown,
    }


# â”€â”€ Update inventory â”€â”€
@router.put("/{item_id}", summary="Update inventory item")
def update_inventory(
    item_id: str,
    data: InventoryUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Update inventory stock. Admin only."""
    item = db.query(InventoryItem).filter(InventoryItem.item_id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Inventory item not found")

    if data.add_quantity is not None:
        item.quantity_total += data.add_quantity
        item.quantity_available += data.add_quantity
    if data.min_stock_level is not None:
        item.min_stock_level = data.min_stock_level
    if data.notes is not None:
        item.notes = data.notes
    if data.color is not None:
        item.color = data.color
    if data.size is not None:
        item.size = data.size

    db.commit()
    db.refresh(item)
    return _to_response(item)


# â”€â”€ Delete inventory item â”€â”€
@router.delete("/{item_id}", summary="Delete inventory item")
def delete_inventory(
    item_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Delete an inventory item. Admin only."""
    item = db.query(InventoryItem).filter(InventoryItem.item_id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    db.delete(item)
    db.commit()
    return {"detail": "Inventory item deleted"}


def _to_response(item: InventoryItem) -> dict:
    return {
        "item_id": item.item_id,
        "item_type": item.item_type,
        "size": item.size,
        "color": item.color,
        "quantity_total": item.quantity_total,
        "quantity_available": item.quantity_available,
        "min_stock_level": item.min_stock_level,
        "is_low_stock": item.quantity_available <= item.min_stock_level,
        "notes": item.notes,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }
