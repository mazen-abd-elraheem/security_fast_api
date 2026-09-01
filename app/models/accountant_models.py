import uuid
from sqlalchemy import Column, String, Float, Boolean, Date, DateTime, Integer, Text, ForeignKey
from datetime import datetime, timezone
from app.core.database import Base


class TaxBracket(Base):
    __tablename__ = "tax_brackets"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    min_amount = Column(Float, nullable=False, default=0)
    max_amount = Column(Float, nullable=True)
    rate = Column(Float, nullable=False, default=0)
    label = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class EmployeeBonus(Base):
    __tablename__ = "employee_bonuses"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    employee_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    bonus_type = Column(String(50), nullable=False)
    amount = Column(Float, default=0)
    notes = Column(Text, nullable=True)
    created_by = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Holiday(Base):
    __tablename__ = "holidays"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(200), nullable=False)
    date = Column(Date, nullable=False, unique=True)
    is_paid = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Termination(Base):
    __tablename__ = "terminations"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    employee_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    termination_date = Column(Date, nullable=False)
    reason = Column(Text, nullable=True)
    final_settlement = Column(Float, default=0)
    status = Column(String(20), default="pending")
    created_by = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
