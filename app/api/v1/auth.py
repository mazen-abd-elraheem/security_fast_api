"""
SecureTrack Platform — Auth Routes
Registration, login (with progressive lockout), token refresh, and logout.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
import logging

logger = logging.getLogger(__name__)

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.database import get_db
from app.core.security import (
    create_access_token, create_refresh_token, verify_token,
    revoke_token, revoke_all_user_tokens,
    is_token_revoked, is_user_tokens_revoked,
)
from app.models.user import User
from app.enums import UserRole
from app.schemas.user import UserCreate, UserResponse
from app.services.user_service import UserService
from app.api.deps import get_current_user, handle_service_exception
from app.core.exceptions import SecureTrackException

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)

# ── Progressive Lockout Delays ──
# After N consecutive failures, lock for this duration
LOCKOUT_DELAYS = {
    1: timedelta(minutes=1),
    2: timedelta(minutes=5),
    3: timedelta(minutes=15),
}
# 4+ failures → 1 hour lockout each time
DEFAULT_LOCKOUT = timedelta(hours=1)


def _get_lockout_duration(fail_count: int) -> timedelta:
    """Get the lockout duration based on consecutive failure count."""
    return LOCKOUT_DELAYS.get(fail_count, DEFAULT_LOCKOUT)


def _check_lockout(user: User):
    """Raise 423 Locked if the user is currently locked out."""
    if user.locked_until:
        locked_until_aware = user.locked_until if user.locked_until.tzinfo else user.locked_until.replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        if locked_until_aware > now_utc:
            remaining = (locked_until_aware - now_utc).total_seconds()
            remaining_min = max(1, int(remaining / 60))
            raise HTTPException(
                status_code=423,  # 423 Locked
                detail=f"Account is temporarily locked due to multiple failed login attempts. "
                       f"Try again in {remaining_min} minute(s).",
            )


def _record_failed_login(user: User, db: Session):
    """Increment failure count and set progressive lockout."""
    user.failed_login_count = (user.failed_login_count or 0) + 1
    
    # Store naive UTC datetime since MySQL drops timezone info
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    user.last_failed_login = now_naive
    lockout_duration = _get_lockout_duration(user.failed_login_count)
    user.locked_until = now_naive + lockout_duration
    db.flush()


def _reset_lockout(user: User, db: Session):
    """Reset lockout counters on successful login."""
    user.failed_login_count = 0
    user.locked_until = None
    user.last_failed_login = None
    db.flush()


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
    OAuth2 password login with progressive lockout protection.

    - **username**: Email address, badge number, or employee code
    - **password**: User password
    - Returns: `access_token`, `refresh_token`, and `token_type`
    - Account locks progressively: 1min → 5min → 15min → 1hr after failures
    """
    # 1) Find the user first (for lockout check)
    if "@" in form_data.username:
        pending_user = UserService.get_by_email(db, form_data.username)
    else:
        from sqlalchemy import or_
        pending_user = db.query(User).filter(
            or_(
                User.badge_number == form_data.username,
                User.employee_code == form_data.username,
            )
        ).first()

    # 2) Check if account is locked out
    if pending_user:
        _check_lockout(pending_user)

    # 3) Check if user exists but has a non-active status
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

    # 4) Authenticate
    user = UserService.authenticate(db, form_data.username, form_data.password)
    if not user:
        # Record failed attempt for lockout
        if pending_user:
            _record_failed_login(pending_user, db)
            db.commit()  # commit the lockout state change
        else:
            # SECURITY (Gap 13.10): Anti-enumeration — perform a dummy bcrypt
            # comparison so the response timing is identical whether the user
            # exists or not. Without this, an attacker can detect valid emails
            # by measuring response latency.
            from app.core.security import verify_password
            verify_password("dummy", "$2b$12$LJ3m4ys3Lg2Rz6YsAnPWvOcPgYb3BYfNn.F3YX1sT9xR5dF5Q8W2y")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email/badge number or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 5) Success — reset lockout counters
    _reset_lockout(user, db)
    db.commit()  # commit the lockout reset

    # 6) Check if MFA is enabled
    if getattr(user, 'totp_enabled', False):
        # Return a partial response indicating MFA is required
        return {
            "mfa_required": True,
            "user_id": user.user_id,
            "message": "Please provide your TOTP code to complete login.",
        }

    # 7) Generate tokens
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
    summary="Refresh access token (with token rotation)",
)
@limiter.limit("20/minute")
def refresh_token(
    request: Request,
    refresh_token: str,
    db: Session = Depends(get_db),
):
    """
    Exchange a valid refresh token for a new access + refresh token pair.
    
    SECURITY (Gap 13.2): Implements refresh-token rotation:
    - Each refresh token can only be used ONCE
    - On use, the old refresh token is revoked and a new one is issued
    - If a revoked refresh token is reused, it signals token theft:
      ALL tokens for that user are revoked, forcing full re-authentication
    """
    payload = verify_token(refresh_token, expected_type="refresh")
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    user_id = payload.get("sub")
    jti = payload.get("jti")

    # ── Reuse Detection (Gap 13.2) ──
    # If this refresh token was already used (revoked), it means someone
    # stole the old token and is replaying it. Revoke ALL tokens for safety.
    if jti and is_token_revoked(jti, db):
        logger.warning(
            "SECURITY: Refresh token reuse detected for user %s (jti=%s). "
            "Revoking all tokens — possible token theft.",
            user_id, jti,
        )
        revoke_all_user_tokens(user_id, reason="refresh_token_reuse_detected", db=db)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Security alert: session compromised. All sessions revoked. Please log in again.",
        )

    # Check if all user tokens were bulk-revoked
    if is_user_tokens_revoked(user_id, db):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="All sessions have been revoked. Please log in again.",
        )

    user = UserService.get_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # SECURITY: Check if user is still active before issuing new token
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account has been deactivated. Please contact your administrator.",
        )

    # ── Rotate: revoke old refresh token, issue new pair ──
    if jti:
        exp = payload.get("exp")
        expires_at = datetime.fromtimestamp(exp, tz=timezone.utc) if exp else (
            datetime.now(timezone.utc) + timedelta(days=7)
        )
        revoke_token(jti, user_id, expires_at, reason="refresh_rotation", db=db)

    token_data = {
        "sub": user.user_id,
        "role": user.role.value if hasattr(user.role, 'value') else user.role,
    }
    new_access_token = create_access_token(data=token_data)
    new_refresh_token = create_refresh_token(data=token_data)

    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
    }


@router.post(
    "/logout",
    summary="Logout and revoke tokens",
)
def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Revoke the current access token so it cannot be reused.
    The Flutter app should also delete stored tokens locally.
    """
    # Get the raw token from the Authorization header
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        payload = verify_token(token)
        if payload:
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
                revoke_token(jti, current_user.user_id, expires_at, "logout", db)

    return {"message": "Logged out successfully"}


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
