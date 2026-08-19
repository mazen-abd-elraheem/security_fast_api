"""
SecureTrack Platform — Uniform Service
Business logic for uniform item tracking, issuance, and admin dashboards.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.uniform_item import UniformItem
from app.models.user import User
from app.schemas.uniform import (
    UniformItemCreate, UniformItemUpdate,
    UniformItemResponse, EmployeeUniformSummary,
)
from app.core.exceptions import NotFoundException, BadRequestException


class UniformService:

    # ── Issue a single uniform item ──
    @staticmethod
    def issue_item(
        db: Session,
        data: UniformItemCreate,
        issued_by_id: Optional[str] = None,
    ) -> UniformItem:
        employee = db.query(User).filter(User.user_id == data.employee_id).first()
        if not employee:
            raise NotFoundException("Employee", data.employee_id)

        item = UniformItem(
            item_id=str(uuid.uuid4()),
            employee_id=data.employee_id,
            item_type=data.item_type.value if hasattr(data.item_type, 'value') else data.item_type,
            size=data.size,
            color=data.color,
            notes=data.notes,
            status="issued",
            issued_date=data.issued_date or datetime.now(timezone.utc),
            issued_by=issued_by_id,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    # ── Issue multiple items at once ──
    @staticmethod
    def bulk_issue(
        db: Session,
        employee_id: str,
        items: List[UniformItemCreate],
        issued_by_id: Optional[str] = None,
    ) -> List[UniformItem]:
        employee = db.query(User).filter(User.user_id == employee_id).first()
        if not employee:
            raise NotFoundException("Employee", employee_id)

        created = []
        for data in items:
            item = UniformItem(
                item_id=str(uuid.uuid4()),
                employee_id=employee_id,
                item_type=data.item_type.value if hasattr(data.item_type, 'value') else data.item_type,
                size=data.size,
                color=data.color,
                notes=data.notes,
                status="issued",
                issued_date=data.issued_date or datetime.now(timezone.utc),
                issued_by=issued_by_id,
            )
            db.add(item)
            created.append(item)

        db.commit()
        for item in created:
            db.refresh(item)
        return created

    # ── Update an item (e.g., mark as returned) ──
    @staticmethod
    def update_item(db: Session, item_id: str, data: UniformItemUpdate) -> UniformItem:
        item = db.query(UniformItem).filter(UniformItem.item_id == item_id).first()
        if not item:
            raise NotFoundException("Uniform item", item_id)

        if data.item_type is not None:
            item.item_type = data.item_type.value if hasattr(data.item_type, 'value') else data.item_type
        if data.size is not None:
            item.size = data.size
        if data.color is not None:
            item.color = data.color
        if data.notes is not None:
            item.notes = data.notes
        if data.status is not None:
            item.status = data.status.value if hasattr(data.status, 'value') else data.status
            # Auto-set returned_date when status changes to returned
            if item.status == "returned" and item.returned_date is None:
                item.returned_date = datetime.now(timezone.utc)
        if data.returned_date is not None:
            item.returned_date = data.returned_date

        db.commit()
        db.refresh(item)
        return item

    # ── Delete an item ──
    @staticmethod
    def delete_item(db: Session, item_id: str) -> None:
        item = db.query(UniformItem).filter(UniformItem.item_id == item_id).first()
        if not item:
            raise NotFoundException("Uniform item", item_id)
        db.delete(item)
        db.commit()

    # ── Get items for one employee ──
    @staticmethod
    def get_employee_items(db: Session, employee_id: str) -> List[UniformItem]:
        return (
            db.query(UniformItem)
            .filter(UniformItem.employee_id == employee_id)
            .order_by(UniformItem.issued_date.desc())
            .all()
        )

    # ── Get a single item ──
    @staticmethod
    def get_item(db: Session, item_id: str) -> UniformItem:
        item = db.query(UniformItem).filter(UniformItem.item_id == item_id).first()
        if not item:
            raise NotFoundException("Uniform item", item_id)
        return item

    # ── Get all items (admin view) ──
    @staticmethod
    def get_all_items(
        db: Session,
        status_filter: Optional[str] = None,
        employee_id: Optional[str] = None,
    ) -> List[UniformItem]:
        q = db.query(UniformItem)
        if status_filter:
            q = q.filter(UniformItem.status == status_filter)
        if employee_id:
            q = q.filter(UniformItem.employee_id == employee_id)
        return q.order_by(UniformItem.issued_date.desc()).all()

    # ── Admin Tracker: active employees with NO uniform ──
    @staticmethod
    def get_employees_without_uniform(db: Session) -> List[dict]:
        """Active employees who have never received any uniform item."""
        # Sub-query: employee IDs that have at least one uniform item
        employees_with_uniform = (
            db.query(UniformItem.employee_id)
            .distinct()
            .subquery()
        )

        # Active employees NOT in that set
        employees = (
            db.query(User)
            .filter(
                User.is_active == True,
                User.role.in_(["guard", "outdoor", "supervisor"]),
                ~User.user_id.in_(db.query(employees_with_uniform.c.employee_id)),
            )
            .order_by(User.name)
            .all()
        )

        return [
            {
                "employee_id": e.user_id,
                "employee_name": e.name,
                "badge_number": e.badge_number,
                "role": e.role if isinstance(e.role, str) else e.role.value,
                "is_active": e.is_active,
                "total_items_issued": 0,
                "total_items_returned": 0,
                "total_items_outstanding": 0,
                "items": [],
            }
            for e in employees
        ]

    # ── Admin Tracker: terminated employees with un-returned uniforms ──
    @staticmethod
    def get_terminated_with_unreturned(db: Session) -> List[dict]:
        """Inactive/terminated employees who still have issued (not returned) uniform items."""
        # Get all inactive employees
        inactive_employees = (
            db.query(User)
            .filter(
                User.is_active == False,
                User.role.in_(["guard", "outdoor", "supervisor"]),
            )
            .all()
        )

        result = []
        for emp in inactive_employees:
            items = (
                db.query(UniformItem)
                .filter(
                    UniformItem.employee_id == emp.user_id,
                    UniformItem.status == "issued",  # Not returned
                )
                .all()
            )
            if items:  # Only include if they have unreturned items
                total_all = db.query(UniformItem).filter(
                    UniformItem.employee_id == emp.user_id
                ).count()
                total_returned = db.query(UniformItem).filter(
                    UniformItem.employee_id == emp.user_id,
                    UniformItem.status == "returned",
                ).count()

                result.append({
                    "employee_id": emp.user_id,
                    "employee_name": emp.name,
                    "badge_number": emp.badge_number,
                    "role": emp.role if isinstance(emp.role, str) else emp.role.value,
                    "is_active": emp.is_active,
                    "total_items_issued": total_all,
                    "total_items_returned": total_returned,
                    "total_items_outstanding": len(items),
                    "items": items,
                })

        return result

    # ── Helper: build response from model ──
    @staticmethod
    def to_response(item: UniformItem) -> UniformItemResponse:
        return UniformItemResponse(
            item_id=item.item_id,
            employee_id=item.employee_id,
            employee_name=item.employee.name if item.employee else None,
            item_type=item.item_type,
            size=item.size,
            color=item.color,
            notes=item.notes,
            status=item.status,
            issued_date=item.issued_date,
            returned_date=item.returned_date,
            issued_by=item.issued_by,
            issuer_name=item.issuer.name if item.issuer else None,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
