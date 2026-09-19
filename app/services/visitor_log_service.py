"""
SecureTrack Platform — Visitor Log Service
Business logic for visit reasons and visitor log entries.
"""
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.models.visitor_log import VisitorLog, VisitReason
from app.schemas.visitor_log import VisitorLogCreate, VisitReasonCreate, VisitReasonUpdate


class VisitorLogService:

    # ── Visit Reasons ─────────────────────────────────────────────────────────

    @staticmethod
    def list_reasons(db: Session, tenant_id: str, include_inactive: bool = False) -> List[VisitReason]:
        q = db.query(VisitReason).filter(VisitReason.tenant_id == tenant_id)
        if not include_inactive:
            q = q.filter(VisitReason.is_active == True)
        return q.order_by(VisitReason.label).all()

    @staticmethod
    def create_reason(db: Session, tenant_id: str, data: VisitReasonCreate) -> VisitReason:
        reason = VisitReason(
            tenant_id=tenant_id,
            label=data.label.strip(),
        )
        db.add(reason)
        db.commit()
        db.refresh(reason)
        return reason

    @staticmethod
    def update_reason(db: Session, reason_id: str, tenant_id: str, data: VisitReasonUpdate) -> Optional[VisitReason]:
        reason = db.query(VisitReason).filter(
            VisitReason.reason_id == reason_id,
            VisitReason.tenant_id == tenant_id,
        ).first()
        if not reason:
            return None
        if data.label is not None:
            reason.label = data.label.strip()
        if data.is_active is not None:
            reason.is_active = data.is_active
        db.commit()
        db.refresh(reason)
        return reason

    @staticmethod
    def delete_reason(db: Session, reason_id: str, tenant_id: str) -> bool:
        reason = db.query(VisitReason).filter(
            VisitReason.reason_id == reason_id,
            VisitReason.tenant_id == tenant_id,
        ).first()
        if not reason:
            return False
        db.delete(reason)
        db.commit()
        return True

    # ── Visitor Logs ─────────────────────────────────────────────────────────

    @staticmethod
    def create_log(
        db: Session,
        tenant_id: str,
        leader_id: str,
        leader_name: str,
        data: VisitorLogCreate,
    ) -> VisitorLog:
        log = VisitorLog(
            tenant_id=tenant_id,
            leader_id=leader_id,
            leader_name=leader_name,
            site_id=data.site_id,
            site_name=data.site_name,
            visitor_name=data.visitor_name.strip(),
            visitor_id_number=data.visitor_id_number,
            visit_reason=data.visit_reason,
            visitor_photo_url=data.visitor_photo_url,
            id_photo_url=data.id_photo_url,
            notes=data.notes,
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

    @staticmethod
    def list_logs(
        db: Session,
        tenant_id: str,
        site_id: Optional[str] = None,
        visit_reason: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ):
        q = db.query(VisitorLog).filter(VisitorLog.tenant_id == tenant_id)

        if site_id:
            q = q.filter(VisitorLog.site_id == site_id)
        if visit_reason:
            q = q.filter(VisitorLog.visit_reason == visit_reason)
        if date_from:
            q = q.filter(VisitorLog.created_at >= date_from)
        if date_to:
            q = q.filter(VisitorLog.created_at <= date_to + " 23:59:59")
        if search:
            q = q.filter(VisitorLog.visitor_name.ilike(f"%{search}%"))

        total = q.count()
        logs = q.order_by(desc(VisitorLog.created_at)).offset((page - 1) * page_size).limit(page_size).all()
        return logs, total
