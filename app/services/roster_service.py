"""
SecureTrack Platform — Roster Service
Guard scheduling — assign guards to shifts on specific dates.
"""
import uuid
from typing import Optional, List
from datetime import date

from sqlalchemy.orm import Session

from app.models.guard_roster import GuardRoster
from app.models.shift import Shift
from app.models.user import User
from app.schemas.roster import RosterCreate, BulkRosterCreate
from app.core.exceptions import NotFoundException, DuplicateException, BadRequestException


class RosterService:
    """Guard scheduling — assign guards to shifts on specific dates."""

    @staticmethod
    def assign_guard(db: Session, roster_data: RosterCreate) -> GuardRoster:
        """Assign a guard to a shift on a specific date."""
        # Validate guard exists and is a guard
        guard = db.query(User).filter(User.user_id == roster_data.guard_id).first()
        if not guard:
            raise NotFoundException("Guard", roster_data.guard_id)
        if guard.role not in ("guard", "outdoor"):
            raise BadRequestException(f"User {guard.name} is not a guard or outdoor user (role: {guard.role})")

        # Validate shift exists
        shift = db.query(Shift).filter(Shift.shift_id == roster_data.shift_id).first()
        if not shift:
            raise NotFoundException("Shift", roster_data.shift_id)

        # Cancel any existing assignment on the same date (allows re-assignment)
        existing = db.query(GuardRoster).filter(
            GuardRoster.guard_id == roster_data.guard_id,
            GuardRoster.assigned_date == roster_data.assigned_date,
            GuardRoster.status != "canceled",
        ).all()
        for old in existing:
            old.status = "canceled"

        db_roster = GuardRoster(
            roster_id=str(uuid.uuid4()),
            guard_id=roster_data.guard_id,
            shift_id=roster_data.shift_id,
            assigned_date=roster_data.assigned_date,
        )
        db.add(db_roster)
        db.commit()
        db.refresh(db_roster)
        return db_roster

    @staticmethod
    def bulk_assign(db: Session, bulk_data: BulkRosterCreate) -> List[GuardRoster]:
        """Assign multiple guards to shifts at once."""
        results = []
        for assignment in bulk_data.assignments:
            roster = RosterService.assign_guard(db, assignment)
            results.append(roster)
        return results

    @staticmethod
    def get_roster_for_site(db: Session, site_id: str, target_date: date) -> list:
        """Get all guard assignments for a site on a specific date."""
        return (
            db.query(GuardRoster)
            .join(Shift, GuardRoster.shift_id == Shift.shift_id)
            .filter(
                Shift.site_id == site_id,
                GuardRoster.assigned_date == target_date,
                GuardRoster.status != "canceled",
            )
            .all()
        )

    @staticmethod
    def get_guard_schedule(
        db: Session,
        guard_id: str,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> list:
        """Get a guard's schedule for a date range."""
        query = db.query(GuardRoster).filter(
            GuardRoster.guard_id == guard_id,
            GuardRoster.status != "canceled",
        )
        if date_from:
            query = query.filter(GuardRoster.assigned_date >= date_from)
        if date_to:
            query = query.filter(GuardRoster.assigned_date <= date_to)

        return query.order_by(GuardRoster.assigned_date.asc()).all()

    @staticmethod
    def remove_assignment(db: Session, roster_id: str) -> None:
        """Cancel a roster assignment."""
        roster = db.query(GuardRoster).filter(GuardRoster.roster_id == roster_id).first()
        if not roster:
            raise NotFoundException("Roster assignment", roster_id)
        roster.status = "canceled"
        db.commit()
