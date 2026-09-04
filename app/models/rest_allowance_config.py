"""
SecureTrack Platform — Rest Allowance Config Model
Stores the rest allowance rate per role, configurable by admin/accountant.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, Boolean, DateTime

from app.core.database import Base


class RestAllowanceConfig(Base):
    __tablename__ = "rest_allowance_config"

    config_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)

    # Role this config applies to (guard, outdoor, leader, supervisor, lady)
    role = Column(String(30), nullable=False, unique=True, index=True)

    # Rate per rest day worked (in EGP)
    rate_per_day = Column(Float, nullable=False, default=0.0)

    is_active = Column(Boolean, nullable=False, default=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<RestAllowanceConfig(role={self.role}, rate={self.rate_per_day})>"
