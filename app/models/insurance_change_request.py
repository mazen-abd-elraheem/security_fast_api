"""
SecureTrack Platform — Insurance Change Request Model
Four-eyes flow for insurance record edits:
Personnel Officer submits a cell change → HR (a different user) approves or rejects.
Approval applies the new value to the users table.
"""
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Index
from datetime import datetime, timezone

from app.core.database import Base


class InsuranceChangeRequest(Base):
    __tablename__ = "insurance_change_requests"
    __table_args__ = (
        Index('ix_icr_user', 'user_id'),
        Index('ix_icr_status', 'status'),
        Index('ix_icr_requested_by', 'requested_by'),
    )

    request_id = Column(String(36), primary_key=True, index=True)

    # Employee whose insurance record is being changed
    user_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    employee_name = Column(String(255), nullable=True)  # Denormalized
    badge_number = Column(String(50), nullable=True)    # Denormalized

    # The change
    field = Column(String(50), nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)

    # pending / approved / rejected
    status = Column(String(20), nullable=False, default="pending")

    # Maker
    requested_by = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    requested_by_name = Column(String(255), nullable=True)

    # Checker
    reviewed_by = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    reviewed_by_name = Column(String(255), nullable=True)
    review_notes = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<InsuranceChangeRequest(id={self.request_id}, field={self.field}, status={self.status})>"
