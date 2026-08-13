"""
SecureTrack Platform — Attendance Service
Records guard attendance during supervisor visits.
"""
import uuid
import logging
from typing import Optional, List
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.attendance_log import AttendanceLog
from app.models.guard_roster import GuardRoster
from app.models.supervisor_visit import SupervisorVisit
from app.models.shift import Shift
from app.models.user import User
from app.schemas.attendance import AttendanceRecord, BulkAttendanceRequest
from app.core.exceptions import NotFoundException, BadRequestException

log = logging.getLogger(__name__)


class AttendanceService:
    """Records guard attendance during supervisor visits."""

    @staticmethod
    def record_attendance(
        db: Session,
        supervisor_id: str,
        visit_id: str,
        record: AttendanceRecord,
    ) -> AttendanceLog:
        """Record attendance for a single guard during a visit."""
        # Validate visit (skip if 'manual' — direct attendance without visit)
        actual_visit_id = None
        if visit_id and visit_id != 'manual':
            visit = db.query(SupervisorVisit).filter(SupervisorVisit.visit_id == visit_id).first()
            if not visit:
                raise NotFoundException("Visit", visit_id)
            if visit.supervisor_id != supervisor_id:
                raise BadRequestException("This is not your visit")
            actual_visit_id = visit_id

        # Validate roster
        roster = db.query(GuardRoster).filter(GuardRoster.roster_id == record.roster_id).first()
        if not roster:
            raise NotFoundException("Roster assignment", record.roster_id)

        # Check for duplicate — match by roster_id and supervisor on same date
        existing = db.query(AttendanceLog).filter(
            AttendanceLog.roster_id == record.roster_id,
            AttendanceLog.supervisor_id == supervisor_id,
        )
        if actual_visit_id:
            existing = existing.filter(AttendanceLog.visit_id == actual_visit_id)
        else:
            # For manual records, check if supervisor already recorded today
            today_start = datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)
            existing = existing.filter(AttendanceLog.recorded_at >= today_start)
        existing = existing.first()

        if existing:
            # Update existing record
            existing.status = record.status.value
            existing.replacement_guard_id = record.replacement_guard_id
            existing.notes = record.notes
            if actual_visit_id:
                existing.visit_id = actual_visit_id
            db.commit()
            db.refresh(existing)
            return existing

        db_log = AttendanceLog(
            log_id=str(uuid.uuid4()),
            roster_id=record.roster_id,
            visit_id=actual_visit_id,
            supervisor_id=supervisor_id,
            status=record.status.value,
            replacement_guard_id=record.replacement_guard_id,
            notes=record.notes,
        )
        db.add(db_log)
        db.commit()
        db.refresh(db_log)
        return db_log

    @staticmethod
    def bulk_record(
        db: Session,
        supervisor_id: str,
        bulk_data: BulkAttendanceRequest,
    ) -> List[AttendanceLog]:
        """Record attendance for multiple guards during a visit."""
        results = []
        for record in bulk_data.records:
            attendance = AttendanceService.record_attendance(
                db, supervisor_id, bulk_data.visit_id, record,
            )
            results.append(attendance)
        return results

    @staticmethod
    def get_attendance_for_site(
        db: Session,
        site_id: str,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> list:
        """Get attendance records for a site within a date range."""
        query = (
            db.query(AttendanceLog)
            .join(SupervisorVisit, AttendanceLog.visit_id == SupervisorVisit.visit_id)
            .filter(SupervisorVisit.site_id == site_id)
        )
        if date_from:
            query = query.filter(AttendanceLog.recorded_at >= datetime.combine(date_from, datetime.min.time()))
        if date_to:
            query = query.filter(AttendanceLog.recorded_at <= datetime.combine(date_to, datetime.max.time()))

        return query.order_by(AttendanceLog.recorded_at.desc()).all()

    @staticmethod
    def get_guard_attendance(
        db: Session,
        guard_id: str,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> list:
        """Get attendance history for a specific guard."""
        query = (
            db.query(AttendanceLog)
            .join(GuardRoster, AttendanceLog.roster_id == GuardRoster.roster_id)
            .filter(GuardRoster.guard_id == guard_id)
        )
        if date_from:
            query = query.filter(AttendanceLog.recorded_at >= datetime.combine(date_from, datetime.min.time()))
        if date_to:
            query = query.filter(AttendanceLog.recorded_at <= datetime.combine(date_to, datetime.max.time()))

        return query.order_by(AttendanceLog.recorded_at.desc()).all()
    @staticmethod
    def get_supervisor_attendance_today(
        db: Session,
        supervisor_id: str,
        target_date: Optional[date] = None,
    ) -> list:
        """Get attendance logs recorded by a specific supervisor for a specific date (defaults to today)."""
        if target_date is None:
            target_date = date.today()
        
        query = (
            db.query(AttendanceLog)
            .filter(AttendanceLog.supervisor_id == supervisor_id)
            .filter(AttendanceLog.recorded_at >= datetime.combine(target_date, datetime.min.time()))
            .filter(AttendanceLog.recorded_at <= datetime.combine(target_date, datetime.max.time()))
        )
        return query.order_by(AttendanceLog.recorded_at.desc()).all()

    @staticmethod
    def get_attendance_summary(
        db: Session,
        site_id: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> dict:
        """Get attendance summary statistics."""
        query = db.query(AttendanceLog)
        if site_id:
            query = (
                query.join(SupervisorVisit, AttendanceLog.visit_id == SupervisorVisit.visit_id)
                .filter(SupervisorVisit.site_id == site_id)
            )
        if date_from:
            query = query.filter(AttendanceLog.recorded_at >= datetime.combine(date_from, datetime.min.time()))
        if date_to:
            query = query.filter(AttendanceLog.recorded_at <= datetime.combine(date_to, datetime.max.time()))

        total = query.count()
        present = query.filter(AttendanceLog.status == "present").count()
        absent = query.filter(AttendanceLog.status == "absent").count()
        late = query.filter(AttendanceLog.status == "late").count()
        replacement = query.filter(AttendanceLog.status == "replacement").count()

        return {
            "total_scheduled": total,
            "total_present": present,
            "total_absent": absent,
            "total_late": late,
            "total_replacement": replacement,
            "attendance_rate": round((present + late + replacement) / total * 100, 1) if total > 0 else 0.0,
        }
