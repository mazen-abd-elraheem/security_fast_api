"""
SecureTrack Platform — Uniform Item Model
Tracks individual uniform pieces issued to employees: type, size, color, dates, condition.
"""
from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class UniformItem(Base):
    __tablename__ = "uniform_items"

    item_id = Column(String(36), primary_key=True, index=True)

    # Which employee received this item
    employee_id = Column(String(36), ForeignKey("users.user_id"), nullable=False, index=True)

    # Link to inventory stock (optional — for items issued from inventory)
    inventory_item_id = Column(String(36), ForeignKey("inventory_items.item_id"), nullable=True)

    # Item details
    item_type = Column(String(30), nullable=False)   # shirt, pants, shoes, belt, cap, jacket, tie, other
    size = Column(String(20), nullable=True)          # XL, 42, L, etc.
    color = Column(String(50), nullable=True)         # black, navy, khaki, etc.
    notes = Column(String(500), nullable=True)        # Any extra notes

    # Status tracking
    status = Column(String(20), nullable=False, default="issued")  # issued, returned, lost, damaged, needs_cleaning

    # Condition tracking
    condition = Column(String(30), nullable=False, default="new")  # new, good, needs_cleaning, damaged, missing, returned_on_termination
    returned_condition = Column(String(30), nullable=True)  # condition when returned

    # Dates
    issued_date = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    returned_date = Column(DateTime, nullable=True)

    # Who issued / recorded this item
    issued_by = Column(String(36), ForeignKey("users.user_id"), nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    employee = relationship("User", foreign_keys=[employee_id], backref="uniform_items")
    issuer = relationship("User", foreign_keys=[issued_by])
    inventory_item = relationship("InventoryItem", foreign_keys=[inventory_item_id])

    def __repr__(self):
        return f"<UniformItem(id={self.item_id}, employee={self.employee_id}, type={self.item_type}, condition={self.condition})>"

