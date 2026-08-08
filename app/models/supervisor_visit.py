"""
SecureTrack Platform — Supervisor Visit Model
Log of physical GPS-verified supervisor check-ins and check-outs at sites.
This is the core anti-fraud record.
"""
from sqlalchemy import Column, String, Float, DateTime, Boolean, Text, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class SupervisorVisit(Base):
    __tablename__ = "supervisor_visits"
    __table_args__ = (
        Index('ix_visits_site_checkin', 'site_id', 'check_in_time'),
        Index('ix_visits_supervisor_date', 'supervisor_id', 'check_in_time'),
    )

    visit_id = Column(String(36), primary_key=True, index=True)
    supervisor_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    site_id = Column(String(36), ForeignKey("sites.site_id", ondelete="CASCADE"), nullable=False, index=True)
    route_id = Column(String(36), ForeignKey("supervisor_routes.route_id", ondelete="SET NULL"), nullable=True, index=True)

    # Check-in data (GPS verified)
    check_in_time = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    check_in_lat = Column(Float, nullable=False)
    check_in_lng = Column(Float, nullable=False)
    distance_from_site = Column(Float, nullable=True)  # Actual distance in meters at check-in

    # Check-out data
    check_out_time = Column(DateTime, nullable=True)
    check_out_lat = Column(Float, nullable=True)
    check_out_lng = Column(Float, nullable=True)

    # Verification
    is_verified = Column(Boolean, nullable=False, default=True)

    # Photo proof (optional liveness check)
    photo_url = Column(String(500), nullable=True)

    # Supervisor notes about the visit
    notes = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    supervisor = relationship("User", back_populates="supervisor_visits", foreign_keys=[supervisor_id])
    site = relationship("Site", back_populates="supervisor_visits")
    route = relationship("SupervisorRoute", back_populates="visits")
    attendance_logs = relationship("AttendanceLog", back_populates="visit", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<SupervisorVisit(visit_id={self.visit_id}, site={self.site_id}, verified={self.is_verified})>"
