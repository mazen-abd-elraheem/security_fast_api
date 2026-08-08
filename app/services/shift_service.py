"""
SecureTrack Platform — Shift Service
Manages guard shift definitions for sites.
"""
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.models.shift import Shift
from app.models.site import Site
from app.schemas.shift import ShiftCreate, ShiftUpdate
from app.core.exceptions import NotFoundException


class ShiftService:
    """Manages shift definitions for sites."""

    @staticmethod
    def create_shift(db: Session, shift_data: ShiftCreate) -> Shift:
        """Create a new shift for a site."""
        site = db.query(Site).filter(Site.site_id == shift_data.site_id).first()
        if not site:
            raise NotFoundException("Site", shift_data.site_id)

        db_shift = Shift(
            shift_id=str(uuid.uuid4()),
            site_id=shift_data.site_id,
            start_time=shift_data.start_time,
            end_time=shift_data.end_time,
            days_of_week=shift_data.days_of_week,
            required_headcount=shift_data.required_headcount,
            label=shift_data.label,
        )
        db.add(db_shift)
        db.commit()
        db.refresh(db_shift)
        return db_shift

    @staticmethod
    def get_shift(db: Session, shift_id: str) -> Shift:
        """Get a shift by ID."""
        shift = db.query(Shift).filter(Shift.shift_id == shift_id).first()
        if not shift:
            raise NotFoundException("Shift", shift_id)
        return shift

    @staticmethod
    def update_shift(db: Session, shift_id: str, update_data: ShiftUpdate) -> Shift:
        """Update shift details."""
        shift = ShiftService.get_shift(db, shift_id)

        if update_data.start_time is not None:
            shift.start_time = update_data.start_time
        if update_data.end_time is not None:
            shift.end_time = update_data.end_time
        if update_data.days_of_week is not None:
            shift.days_of_week = update_data.days_of_week
        if update_data.required_headcount is not None:
            shift.required_headcount = update_data.required_headcount
        if update_data.label is not None:
            shift.label = update_data.label
        if update_data.is_active is not None:
            shift.is_active = update_data.is_active

        db.commit()
        db.refresh(shift)
        return shift

    @staticmethod
    def get_shifts_for_site(db: Session, site_id: str) -> list:
        """Get all shifts for a site."""
        site = db.query(Site).filter(Site.site_id == site_id).first()
        if not site:
            raise NotFoundException("Site", site_id)

        return db.query(Shift).filter(
            Shift.site_id == site_id,
            Shift.is_active == True,
        ).order_by(Shift.start_time.asc()).all()

    @staticmethod
    def delete_shift(db: Session, shift_id: str) -> None:
        """Deactivate a shift."""
        shift = ShiftService.get_shift(db, shift_id)
        shift.is_active = False
        db.commit()
