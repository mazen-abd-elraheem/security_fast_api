"""
SecureTrack — Custody Disbursement Model
Tracks salary/advance/clothes/device handoff from supervisor to guard,
with guard confirmation (amount + photo).
"""
from sqlalchemy import Column, String, Float, DateTime, Text, ForeignKey, Index, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class CustodyDisbursement(Base):
    __tablename__ = "custody_disbursements"
    __table_args__ = (
        Index('ix_custody_supervisor', 'supervisor_id'),
        Index('ix_custody_guard', 'guard_id'),
        Index('ix_custody_status', 'status'),
        Index('ix_custody_type', 'custody_type'),
    )

    disbursement_id = Column(String(36), primary_key=True, index=True)

    # Type: salary | cash_advance | clothes | device
    custody_type = Column(String(20), nullable=False)

    # Reference to source record (optional FK by type)
    ref_id = Column(String(36), nullable=True)  # payroll_id, advance_id, clothes_request_id, etc.

    # Supervisor who distributes
    supervisor_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    supervisor_name = Column(String(255), nullable=True)

    # Guard who receives
    guard_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    guard_name = Column(String(255), nullable=False)
    guard_badge = Column(String(50), nullable=True)

    # Site context
    site_id = Column(String(36), ForeignKey("sites.site_id", ondelete="SET NULL"), nullable=True)
    site_name = Column(String(255), nullable=True)

    # Disbursement details
    amount = Column(Float, nullable=True, default=0.0)     # EGP for salary/advance
    description = Column(Text, nullable=True)              # e.g. "Salary March 2026"

    # Status lifecycle:
    #   Cash types (salary / cash_advance):
    #       pending_admin → (admin approves + credit deducted) → pending_handoff
    #   Item types (clothes / device):
    #       pending_hr → (HR approves) → pending_issue → (personnel issues to supervisor)
    #       → pending_receipt → (supervisor confirms receipt) → pending_handoff
    #   Then (all types):
    #       pending_handoff → handed (supervisor) → confirmed | disputed (guard)
    #   Terminal: rejected (admin/HR), cancelled (admin resolves a dispute)
    status = Column(String(20), nullable=False, default="pending_handoff")

    # Who requested it (accountant for cash, personnel officer for items)
    requested_by = Column(String(36), nullable=True)
    requested_by_name = Column(String(255), nullable=True)

    # Approval step (admin for cash types, HR for item types)
    approved_by = Column(String(36), nullable=True)
    approved_by_name = Column(String(255), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approval_notes = Column(Text, nullable=True)

    # Transfer method credit deducted when admin approves a cash disbursement
    transfer_method_id = Column(String(36), nullable=True)
    transfer_method_name = Column(String(100), nullable=True)
    credit_deducted = Column(Float, nullable=True, default=0.0)

    # Item flow: personnel officer issues to supervisor, supervisor confirms receipt
    issued_at = Column(DateTime, nullable=True)
    supervisor_received_at = Column(DateTime, nullable=True)

    # Supervisor handoff
    handed_at = Column(DateTime, nullable=True)
    supervisor_notes = Column(Text, nullable=True)

    # Guard confirmation
    guard_confirmed_amount = Column(Float, nullable=True)
    guard_photo_url = Column(String(500), nullable=True)
    guard_notes = Column(Text, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    supervisor = relationship("User", foreign_keys=[supervisor_id])
    guard = relationship("User", foreign_keys=[guard_id])

    def __repr__(self):
        return f"<CustodyDisbursement(type={self.custody_type}, guard={self.guard_name}, status={self.status})>"
