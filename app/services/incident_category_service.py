"""
SecureTrack Platform — Incident Category Service
CRUD for admin-managed dynamic incident categories.
"""
import uuid
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.incident_category import IncidentCategory
from app.schemas.incident_category import IncidentCategoryCreate, IncidentCategoryUpdate
from app.core.exceptions import NotFoundException


class IncidentCategoryService:

    @staticmethod
    def create_category(db: Session, data: IncidentCategoryCreate) -> IncidentCategory:
        cat = IncidentCategory(
            category_id=str(uuid.uuid4()),
            name=data.name.strip(),
            severity=data.severity,
            corrective_action=data.corrective_action,
            is_active=True,
        )
        cat.alert_roles = data.alert_roles
        db.add(cat)
        db.commit()
        db.refresh(cat)
        return cat

    @staticmethod
    def list_categories(db: Session, include_inactive: bool = False) -> List[IncidentCategory]:
        query = db.query(IncidentCategory)
        if not include_inactive:
            query = query.filter(IncidentCategory.is_active == True)
        return query.order_by(IncidentCategory.name).all()

    @staticmethod
    def get_category(db: Session, category_id: str) -> IncidentCategory:
        cat = db.query(IncidentCategory).filter(
            IncidentCategory.category_id == category_id
        ).first()
        if not cat:
            raise NotFoundException("IncidentCategory", category_id)
        return cat

    @staticmethod
    def update_category(
        db: Session, category_id: str, data: IncidentCategoryUpdate
    ) -> IncidentCategory:
        cat = IncidentCategoryService.get_category(db, category_id)
        if data.name is not None:
            cat.name = data.name.strip()
        if data.severity is not None:
            cat.severity = data.severity
        if data.corrective_action is not None:
            cat.corrective_action = data.corrective_action
        if data.alert_roles is not None:
            cat.alert_roles = data.alert_roles
        if data.is_active is not None:
            cat.is_active = data.is_active
        db.commit()
        db.refresh(cat)
        return cat

    @staticmethod
    def delete_category(db: Session, category_id: str) -> None:
        """Soft delete — sets is_active = False."""
        cat = IncidentCategoryService.get_category(db, category_id)
        cat.is_active = False
        db.commit()
