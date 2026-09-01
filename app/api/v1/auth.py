"""
SecureTrack Platform — Auth Routes
Registration, login, and token refresh.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.database import get_db
from app.core.security import create_access_token, create_refresh_token, verify_token
from app.models.user import User
from app.enums import UserRole
from app.schemas.user import UserCreate, UserResponse
from app.services.user_service import UserService
from app.api.deps import get_current_user, handle_service_exception
from app.core.exceptions import SecureTrackException

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user (pending admin approval)",
)
@limiter.limit("10/minute")
def register(request: Request, user_data: UserCreate, db: Session = Depends(get_db)):
    """
    Register a new user with SecureTrack.

    - **role**: `guard`, `supervisor`, `client`, or `admin`
    - Password must contain uppercase, lowercase, digit, and special character
    - Account starts as **inactive** and requires admin approval before login
    """
    try:
        user = UserService.create_user(db, user_data)
        return {
            "message": "Registration successful. Your account is pending admin approval.",
            "user_id": user.user_id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "is_active": user.is_active,
            "status": user.status if isinstance(user.status, str) else user.status.value,
        }
    except SecureTrackException as e:
        handle_service_exception(e)


@router.post(
    "/login",
    summary="Login and get JWT tokens",
)
@limiter.limit("10/minute")
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """
    OAuth2 password login.

    - **username**: Email address, badge number, or employee code
    - **password**: User password
    - Returns: `access_token`, `refresh_token`, and `token_type`
    """
    # First check if user exists but has a non-active status
    if "@" in form_data.username:
        pending_user = UserService.get_by_email(db, form_data.username)
    else:
        pending_user = db.query(User).filter(User.badge_number == form_data.username).first()
        if not pending_user:
            pending_user = db.query(User).filter(User.employee_code == form_data.username).first()
    if pending_user and not pending_user.is_active:
        user_status = getattr(pending_user, 'status', None)
        if user_status == 'rejected':
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account registration has been rejected. Please contact your administrator.",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending admin approval. Please wait for activation.",
        )

    user = UserService.authenticate(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email/badge number or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_data = {
        "sub": user.user_id,
        "role": user.role.value if hasattr(user.role, 'value') else user.role,
    }
    access_token = create_access_token(data=token_data)
    refresh_token = create_refresh_token(data=token_data)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user_id": user.user_id,
        "name": user.name,
        "email": user.email,
        "phone_number": user.phone_number,
        "role": user.role.value if hasattr(user.role, 'value') else user.role,
    }


@router.post(
    "/refresh",
    summary="Refresh access token",
)
@limiter.limit("20/minute")
def refresh_token(
    request: Request,
    refresh_token: str,
    db: Session = Depends(get_db),
):
    """Exchange a valid refresh token for a new access token."""
    payload = verify_token(refresh_token, expected_type="refresh")
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    user_id = payload.get("sub")
    user = UserService.get_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    token_data = {
        "sub": user.user_id,
        "role": user.role.value if hasattr(user.role, 'value') else user.role,
    }
    new_access_token = create_access_token(data=token_data)

    return {
        "access_token": new_access_token,
        "token_type": "bearer",
    }


@router.get(
    "/activation-status",
    summary="Check account activation status",
)
@limiter.limit("20/minute")
def check_activation_status(
    request: Request,
    email: str,
    db: Session = Depends(get_db),
):
    """
    Check whether a registered account has been activated by an admin.

    - Returns `pending` if the account is still awaiting admin approval.
    - Returns `active` if the admin has activated the account.
    """
    try:
        return UserService.get_activation_status(db, email)
    except SecureTrackException as e:
        handle_service_exception(e)
