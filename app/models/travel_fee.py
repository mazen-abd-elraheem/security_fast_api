"""
SecureTrack — Travel Fee Model
Simple lookup table: from_site → to_site = fee amount.
"""
from sqlalchemy import Column, String, Float, DateTime, Boolean
from datetime import datetime, timezone

from app.core.database import Base


class TravelFee(Base):
    __tablename__ = "travel_fees"

    fee_id = Column(String(36), primary_key=True, index=True)
    from_site_name = Column(String(255), nullable=False)
    to_site_name = Column(String(255), nullable=False)
    amount = Column(Float, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<TravelFee({self.from_site_name} → {self.to_site_name} = {self.amount})>"
