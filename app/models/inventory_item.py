"""
SecureTrack Platform — Inventory Item Model
Tracks clothing/uniform stock managed by Admin.
"""
from sqlalchemy import Column, String, Integer, DateTime
from datetime import datetime, timezone

from app.core.database import Base


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    item_id = Column(String(36), primary_key=True, index=True)

    # Item details
    item_type = Column(String(30), nullable=False)   # shirt, pants, shoes, belt, cap, jacket, tie, other
    size = Column(String(20), nullable=True)          # XL, L, M, S, 42, etc.
    color = Column(String(50), nullable=True)         # black, navy, khaki, etc.

    # Stock tracking
    quantity_total = Column(Integer, nullable=False, default=0)       # Total stock ever added
    quantity_available = Column(Integer, nullable=False, default=0)   # Current available
    min_stock_level = Column(Integer, nullable=False, default=5)      # Alert threshold

    notes = Column(String(500), nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<InventoryItem(id={self.item_id}, type={self.item_type}, size={self.size}, avail={self.quantity_available})>"
