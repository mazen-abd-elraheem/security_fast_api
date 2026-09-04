"""
SecureTrack — Travel Allowance Entry Model
Individual trip records for supervisor travel allowance tracking.
Auto-generated from SupervisorVisit data, editable by Admin/Accountant/CEO.
"""
from sqlalchemy import Column, String, Float, Date, DateTime, Text, ForeignKey, Index, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class TravelAllowanceEntry(Base):
    __tablename__ = "travel_allowance_entries"
    __table_args__ = (
        Index('ix_travel_allowance_supervisor_date', 'supervisor_id', 'trip_date'),
    )

    entry_id = Column(String(36), primary_key=True, index=True)
    supervisor_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)

    trip_date = Column(Date, nullable=False)
    trip_number = Column(String(50), nullable=False)  # Auto-generated: T-YYYYMMDD-001
    from_site = Column(String(255), nullable=False)   # من — origin site/base
    to_site = Column(String(255), nullable=False)      # الي — destination site/base
    amount = Column(Float, nullable=False, default=0.0)  # المبلغ — from TravelFee rules, editable
    notes = Column(Text, nullable=True)                # ملاحظه — set by admin/accountant/CEO
    is_active = Column(Boolean, nullable=False, default=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    supervisor = relationship("User", foreign_keys=[supervisor_id])

    def __repr__(self):
        return f"<TravelAllowanceEntry(id={self.entry_id}, sup={self.supervisor_id}, {self.from_site}→{self.to_site}={self.amount})>"
