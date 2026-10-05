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

    ASSIGNABLE_ROLES = ("guard", "outdoor", "lady")

    @staticmethod
    def _role(user) -> str:
        r = getattr(user, "role", "")
        return (r.value if hasattr(r, "value") else str(r or "")).lower()

    @staticmethod
    def upsert_assignment(
        db: Session,
        guard: User,
        shift: Shift,
        start_d: date,
        end_d: Optional[date] = None,
        supervisor_id: Optional[str] = None,
        leader_id: Optional[str] = None,
    ) -> tuple:
        """
        THE single rule-set for putting a guard on a shift.
        Used by the manual assign dialog AND by Excel / wizard imports so both
        produce identical rows.

        - The guard's other active bookings that overlap [start_d, end_d] are
          canceled (the new assignment replaces them — same as the UI warning).
        - If the guard already has an overlapping booking on the SAME shift it
          is updated in place instead of duplicated (idempotent re-imports).
        Does NOT commit; caller commits.
        Returns (action, roster) where action ∈ {"created", "updated", "unchanged"}.
        """
        end_d = end_d or start_d
        if end_d < start_d:
            start_d, end_d = end_d, start_d

        active = db.query(GuardRoster).filter(
            GuardRoster.guard_id == guard.user_id,
            GuardRoster.status != "canceled",
        ).all()

        same = None
        for r in active:
            r_start = r.start_date or r.assigned_date
            r_end = r.end_date or r.assigned_date
            if not (r_start <= end_d and r_end >= start_d):
                continue  # no overlap → untouched
            if r.shift_id == shift.shift_id and same is None:
                same = r
            else:
                r.status = "canceled"

        if same is not None:
            changed = False
            for attr, val in (
                ("assigned_date", start_d),
                ("start_date", start_d),
                ("end_date", end_d),
                ("status", "scheduled"),
            ):
                if getattr(same, attr) != val:
                    setattr(same, attr, val)
                    changed = True
            if supervisor_id and same.supervisor_id != supervisor_id:
                same.supervisor_id = supervisor_id
                changed = True
            if leader_id and same.leader_id != leader_id:
                same.leader_id = leader_id
                changed = True
            db.flush()
            return ("updated" if changed else "unchanged", same)

        roster = GuardRoster(
            roster_id=str(uuid.uuid4()),
            guard_id=guard.user_id,
            shift_id=shift.shift_id,
            assigned_date=start_d,
            start_date=start_d,
            end_date=end_d,
            supervisor_id=supervisor_id,
            leader_id=leader_id,
            status="scheduled",
        )
        db.add(roster)
        db.flush()
        return ("created", roster)

    @staticmethod
    def assign_guard(db: Session, roster_data: RosterCreate) -> GuardRoster:
        """Assign a guard to a shift for a date / date range."""
        # Validate guard exists and is a guard
        guard = db.query(User).filter(User.user_id == roster_data.guard_id).first()
        if not guard:
            raise NotFoundException("Guard", roster_data.guard_id)
        if RosterService._role(guard) not in RosterService.ASSIGNABLE_ROLES:
            raise BadRequestException(f"User {guard.name} is not a guard or outdoor user (role: {guard.role})")

        # Validate shift exists
        shift = db.query(Shift).filter(Shift.shift_id == roster_data.shift_id).first()
        if not shift:
            raise NotFoundException("Shift", roster_data.shift_id)

        start_d = roster_data.start_date or roster_data.assigned_date
        end_d = roster_data.end_date or start_d
        _, db_roster = RosterService.upsert_assignment(db, guard, shift, start_d, end_d)
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
        """Get all guard assignments for a site on a specific date.

        Includes assignments created for that exact date AND assignments whose
        start_date/end_date range covers the date (e.g. created by CSV import).
        """
        from sqlalchemy import or_, and_
        rows = (
            db.query(GuardRoster)
            .join(Shift, GuardRoster.shift_id == Shift.shift_id)
            .filter(
                Shift.site_id == site_id,
                GuardRoster.status != "canceled",
                or_(
                    GuardRoster.assigned_date == target_date,
                    and_(
                        GuardRoster.start_date.isnot(None),
                        GuardRoster.start_date <= target_date,
                        or_(GuardRoster.end_date.is_(None), GuardRoster.end_date >= target_date),
                    ),
                ),
            )
            .all()
        )
        # De-duplicate per guard+shift, preferring the exact-date row
        best = {}
        for r in rows:
            key = (r.guard_id, r.shift_id)
            exact = str(r.assigned_date) == str(target_date)
            if key not in best or (exact and str(best[key].assigned_date) != str(target_date)):
                best[key] = r
        return list(best.values())

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
