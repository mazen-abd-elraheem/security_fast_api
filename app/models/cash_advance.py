"""
SecureTrack Platform — Cash Advance Model
Tracks cash advance requests from leaders/supervisors for guards.
Approval chain: Leader/Supervisor → Ops Manager → Admin → CEO.
"""
from sqlalchemy import Column, String, Float, Integer, DateTime, Text, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class CashAdvance(Base):
    __tablename__ = "cash_advances"
    __table_args__ = (
        Index('ix_cash_advance_guard', 'guard_id'),
        Index('ix_cash_advance_leader', 'leader_id'),
        Index('ix_cash_advance_status', 'status'),
        Index('ix_cash_advance_supervisor', 'supervisor_id'),
        Index('ix_cash_advance_ops', 'ops_manager_id'),
    )

    advance_id = Column(String(36), primary_key=True, index=True)

    # Who is the advance for
    guard_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    guard_name = Column(String(255), nullable=False)  # Denormalized for display
    guard_code = Column(String(50), nullable=True)  # badge_number of the guard

    # Who requested it
    leader_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    leader_name = Column(String(255), nullable=False)  # Denormalized for display

    # Which site
    site_id = Column(String(36), ForeignKey("sites.site_id", ondelete="CASCADE"), nullable=False)
    site_name = Column(String(255), nullable=False)  # Denormalized for display

    # Cash advance details
    amount = Column(Float, nullable=False)  # Total requested amount
    installment_months = Column(Integer, nullable=False, default=1)  # Over how many months
    amount_per_month = Column(Float, nullable=False)  # amount / installment_months

    # Approval chain status: pending → supervisor_approved/rejected → admin_approved/rejected/modified
    status = Column(String(30), nullable=False, default="pending")

    # Supervisor review
    supervisor_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    supervisor_notes = Column(Text, nullable=True)
    supervisor_reviewed_at = Column(DateTime, nullable=True)

    # Ops Manager review (new step)
    ops_manager_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    ops_manager_notes = Column(Text, nullable=True)
    ops_reviewed_at = Column(DateTime, nullable=True)

    # Admin review
    admin_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    admin_notes = Column(Text, nullable=True)
    admin_reviewed_at = Column(DateTime, nullable=True)
    approved_amount = Column(Float, nullable=True)  # If admin modifies the amount

    # CEO final review
    ceo_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    ceo_notes = Column(Text, nullable=True)
    ceo_reviewed_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    guard = relationship("User", foreign_keys=[guard_id])
    leader = relationship("User", foreign_keys=[leader_id])
    supervisor = relationship("User", foreign_keys=[supervisor_id])
    ops_manager = relationship("User", foreign_keys=[ops_manager_id])
    admin = relationship("User", foreign_keys=[admin_id])
    ceo = relationship("User", foreign_keys=[ceo_id])
    site = relationship("Site", foreign_keys=[site_id])

    def __repr__(self):
        return f"<CashAdvance(id={self.advance_id}, guard={self.guard_name}, amount={self.amount}, status={self.status})>"
