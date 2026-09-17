"""
SecureTrack Platform — Incident Category Routes
Admin CRUD for dynamic incident categories.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user, require_role
from app.models.user import User
from app.enums import UserRole
from app.schemas.incident_category import (
    IncidentCategoryCreate,
    IncidentCategoryUpdate,
    IncidentCategoryResponse,
    IncidentCategoryListResponse,
)
from app.services.incident_category_service import IncidentCategoryService
from app.core.exceptions import SecureTrackException
from app.api.deps import handle_service_exception

router = APIRouter()


def _to_response(cat) -> IncidentCategoryResponse:
    return IncidentCategoryResponse(
        category_id=cat.category_id,
        name=cat.name,
        severity=cat.severity,
        corrective_action=cat.corrective_action,
        alert_roles=cat.alert_roles,
        is_active=cat.is_active,
        created_at=cat.created_at,
    )


@router.get(
    "",
    response_model=IncidentCategoryListResponse,
    summary="List incident categories",
)
def list_categories(
    include_inactive: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all active incident categories (any authenticated user)."""
    cats = IncidentCategoryService.list_categories(
        db,
        include_inactive=include_inactive if current_user.role == UserRole.ADMIN else False,
    )
    return IncidentCategoryListResponse(
        categories=[_to_response(c) for c in cats],
        total=len(cats),
    )


@router.post(
    "",
    response_model=IncidentCategoryResponse,
    status_code=201,
    summary="Create incident category",
)
def create_category(
    data: IncidentCategoryCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Admin creates a new incident category."""
    try:
        cat = IncidentCategoryService.create_category(db, data)
        return _to_response(cat)
    except SecureTrackException as e:
        handle_service_exception(e)


@router.get(
    "/{category_id}",
    response_model=IncidentCategoryResponse,
    summary="Get incident category",
)
def get_category(
    category_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        cat = IncidentCategoryService.get_category(db, category_id)
        return _to_response(cat)
    except SecureTrackException as e:
        handle_service_exception(e)


@router.put(
    "/{category_id}",
    response_model=IncidentCategoryResponse,
    summary="Update incident category",
)
def update_category(
    category_id: str,
    data: IncidentCategoryUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    try:
        cat = IncidentCategoryService.update_category(db, category_id, data)
        return _to_response(cat)
    except SecureTrackException as e:
        handle_service_exception(e)


@router.delete(
    "/{category_id}",
    status_code=204,
    summary="Delete (soft) incident category",
)
def delete_category(
    category_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    try:
        IncidentCategoryService.delete_category(db, category_id)
    except SecureTrackException as e:
        handle_service_exception(e)
