"""
SecureTrack Platform — Site Model
Physical locations requiring security coverage with geofence coordinates.
"""
from sqlalchemy import Column, String, Float, Integer, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class Site(Base):
    __tablename__ = "sites"

    site_id = Column(String(36), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    address = Column(String(500), nullable=True)

    # Geofence center point
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)

    # Geofence radius in meters (check-in must be within this distance)
    radius_meters = Column(Integer, nullable=False, default=100)

    # Regional grouping
    region = Column(String(100), nullable=True, index=True)

    # Status: active, inactive, maintenance
    status = Column(String(20), nullable=False, default="active")

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    shifts = relationship("Shift", back_populates="site", cascade="all, delete-orphan")
    supervisor_routes = relationship("SupervisorRoute", back_populates="site")
    supervisor_visits = relationship("SupervisorVisit", back_populates="site")
    incidents = relationship("Incident", back_populates="site")

    def __repr__(self):
        return f"<Site(site_id={self.site_id}, name={self.name}, radius={self.radius_meters}m)>"
