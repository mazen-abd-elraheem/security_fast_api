"""
SecureTrack Platform - Transfer Method Credit Models
"""
import uuid
from sqlalchemy import Column, String, Float, DateTime, Text, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.core.database import Base


class TransferMethodCredit(Base):
    __tablename__ = "transfer_method_credits"
    __table_args__ = (Index("ix_tmc_method", "transfer_method_id", unique=True),)

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    transfer_method_id = Column(String(36), ForeignKey("transfer_methods.id", ondelete="CASCADE"), nullable=False, unique=True)
    transfer_method_name = Column(String(100), nullable=False)
    balance = Column(Float, nullable=False, default=0.0)
    total_topped_up = Column(Float, nullable=False, default=0.0)
    total_deducted = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    transfer_method = relationship("TransferMethod", foreign_keys=[transfer_method_id])
    logs = relationship("TransferMethodCreditLog", back_populates="credit", cascade="all, delete-orphan")
    top_up_requests = relationship("TransferMethodTopUpRequest", back_populates="credit", cascade="all, delete-orphan")


class TransferMethodCreditLog(Base):
    __tablename__ = "transfer_method_credit_logs"
    __table_args__ = (Index("ix_tmcl_credit", "credit_id"), Index("ix_tmcl_created", "created_at"),)

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    credit_id = Column(String(36), ForeignKey("transfer_method_credits.id", ondelete="CASCADE"), nullable=False)
    operation = Column(String(20), nullable=False)
    amount = Column(Float, nullable=False)
    balance_after = Column(Float, nullable=False)
    reference = Column(String(255), nullable=True)
    actor_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    actor_name = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    credit = relationship("TransferMethodCredit", back_populates="logs")
    actor = relationship("User", foreign_keys=[actor_id])


class TransferMethodTopUpRequest(Base):
    __tablename__ = "transfer_method_top_up_requests"
    __table_args__ = (Index("ix_tmtur_credit", "credit_id"), Index("ix_tmtur_status", "status"),)

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    credit_id = Column(String(36), ForeignKey("transfer_method_credits.id", ondelete="CASCADE"), nullable=False)
    requested_amount = Column(Float, nullable=False)
    reference = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    requested_by = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    requested_by_name = Column(String(255), nullable=True)
    requested_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    reviewed_by = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    reviewed_by_name = Column(String(255), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_notes = Column(Text, nullable=True)

    credit = relationship("TransferMethodCredit", back_populates="top_up_requests")
    requestor = relationship("User", foreign_keys=[requested_by])
    reviewer = relationship("User", foreign_keys=[reviewed_by])