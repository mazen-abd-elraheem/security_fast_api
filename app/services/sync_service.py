"""
SecureTrack Platform — Sync Service
Handles offline data push from mobile supervisors.
"""
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session

from app.schemas.sync import SyncPushRequest, SyncResultItem
from app.services.visit_service import VisitService
from app.services.attendance_service import AttendanceService
from app.schemas.attendance import AttendanceRecord
from app.core.config import get_settings
from app.core.exceptions import SecureTrackException, OfflineSyncExpiredException
from app.enums import AttendanceStatus

settings = get_settings()
log = logging.getLogger(__name__)


class SyncService:
    """Handles offline data push from mobile supervisors."""

    @staticmethod
    def push_offline_data(db: Session, supervisor_id: str, sync_data: SyncPushRequest) -> dict:
        """
        Process cached offline data pushed from the mobile app.
        Each record is processed independently — failures don't block others.
        """
        results = []
        total_processed = 0
        total_failed = 0
        now = datetime.now(timezone.utc)
        max_age = timedelta(hours=settings.OFFLINE_SYNC_MAX_AGE_HOURS)

        # Process visits
        for i, visit_record in enumerate(sync_data.visits):
            try:
                # Check age
                if now - visit_record.check_in_time.replace(tzinfo=timezone.utc) > max_age:
                    raise OfflineSyncExpiredException(settings.OFFLINE_SYNC_MAX_AGE_HOURS)

                visit = VisitService.check_in(
                    db,
                    supervisor_id=supervisor_id,
                    site_id=visit_record.site_id,
                    latitude=visit_record.latitude,
                    longitude=visit_record.longitude,
                    photo_url=visit_record.photo_url,
                    notes=f"[Offline Sync] {visit_record.notes or ''}".strip(),
                )

                # Handle checkout if provided
                if visit_record.check_out_time:
                    VisitService.check_out(
                        db,
                        visit_id=visit.visit_id,
                        supervisor_id=supervisor_id,
                        latitude=visit_record.latitude,
                        longitude=visit_record.longitude,
                    )

                results.append(SyncResultItem(
                    index=i, record_type="visit", success=True, created_id=visit.visit_id,
                ))
                total_processed += 1

            except SecureTrackException as e:
                results.append(SyncResultItem(
                    index=i, record_type="visit", success=False, error=e.message,
                ))
                total_failed += 1
                log.warning("Sync visit failed [%d]: %s", i, e.message)
            except Exception as e:
                results.append(SyncResultItem(
                    index=i, record_type="visit", success=False, error=str(e),
                ))
                total_failed += 1

        # Process attendance records
        for j, att_record in enumerate(sync_data.attendance_records):
            idx = len(sync_data.visits) + j
            try:
                if now - att_record.recorded_at.replace(tzinfo=timezone.utc) > max_age:
                    raise OfflineSyncExpiredException(settings.OFFLINE_SYNC_MAX_AGE_HOURS)

                record = AttendanceRecord(
                    roster_id=att_record.roster_id,
                    status=AttendanceStatus(att_record.status),
                    replacement_guard_id=att_record.replacement_guard_id,
                    notes=f"[Offline Sync] {att_record.notes or ''}".strip(),
                )

                # We need a visit_id — find the most recent visit by this supervisor
                from app.models.supervisor_visit import SupervisorVisit
                recent_visit = (
                    db.query(SupervisorVisit)
                    .filter(SupervisorVisit.supervisor_id == supervisor_id)
                    .order_by(SupervisorVisit.check_in_time.desc())
                    .first()
                )
                if not recent_visit:
                    raise SecureTrackException("No visit found to associate attendance with")

                att = AttendanceService.record_attendance(
                    db, supervisor_id, recent_visit.visit_id, record,
                )
                results.append(SyncResultItem(
                    index=idx, record_type="attendance", success=True, created_id=att.log_id,
                ))
                total_processed += 1

            except SecureTrackException as e:
                results.append(SyncResultItem(
                    index=idx, record_type="attendance", success=False, error=e.message,
                ))
                total_failed += 1
            except Exception as e:
                results.append(SyncResultItem(
                    index=idx, record_type="attendance", success=False, error=str(e),
                ))
                total_failed += 1

        return {
            "total_received": len(sync_data.visits) + len(sync_data.attendance_records),
            "total_processed": total_processed,
            "total_failed": total_failed,
            "results": results,
        }
