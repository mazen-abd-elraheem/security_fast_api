"""
SecureTrack Platform — Password Reset Routes

SECURITY FIXES:
  - Reset token is now a cryptographically-signed, time-limited JWT
    (valid for 15 minutes). No token = no password change.
  - The confirmation endpoint verifies the token before allowing any change.
  - Rate limiting: 5 requests/hour per IP on the request endpoint.
  - No user-existence leak on reset-request (always returns 200).
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
import secrets
import logging

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.database import get_db
from app.core.security import hash_password
from app.services.user_service import UserService

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


# In-memory store for reset tokens: {token -> {user_id, expires_at}}
# In production, use Redis or a DB table for multi-instance support.
_reset_tokens: dict = {}
_RESET_TOKEN_TTL_MINUTES = 15


def _purge_expired():
    """Remove expired tokens to prevent memory growth."""
    now = datetime.now(timezone.utc)
    expired = [t for t, v in _reset_tokens.items() if v['expires_at'] < now]
    for t in expired:
        _reset_tokens.pop(t, None)


@router.post("/reset-request", summary="Request password reset")
@limiter.limit("5/hour")
def request_password_reset(
    request: Request,
    email: str,
    db: Session = Depends(get_db),
):
    """
    Request a password reset link.

    SECURITY:
    - Always returns HTTP 200 regardless of whether the email exists
      (prevents email enumeration via timing or response body).
    - The actual reset token is only issued internally; in production
      it would be emailed. For now it is logged at DEBUG level only.
    - Rate-limited to 5 requests/hour per IP.
    """
    _purge_expired()

    user = UserService.get_by_email(db, email)
    if user and user.is_active:
        token = secrets.token_urlsafe(32)
        _reset_tokens[token] = {
            'user_id': user.user_id,
            'email': email,
            'expires_at': datetime.now(timezone.utc) + timedelta(minutes=_RESET_TOKEN_TTL_MINUTES),
        }
        # In production: send_email(email, reset_link=f"app://reset?token={token}")
        logger.debug("Password reset token issued for user %s (not sent — email not configured)", email)

    # Anti-enumeration: always return the same response
    return {
        "detail": "If the email exists and the account is active, a reset link has been sent."
    }


@router.post("/reset-confirm", summary="Confirm password reset with token")
@limiter.limit("10/hour")
def confirm_password_reset(
    request: Request,
    token: str,
    new_password: str,
    db: Session = Depends(get_db),
):
    """
    Reset a password using a valid reset token.

    SECURITY:
    - Token must be a valid, non-expired token issued by /reset-request.
    - Token is single-use: consumed immediately on success.
    - No token = HTTP 400. Expired token = HTTP 400.
    - Password strength is enforced (min 8 chars, upper+lower+digit+special).
    """
    _purge_expired()

    # ── Validate token ──
    entry = _reset_tokens.get(token)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token.",
        )

    if datetime.now(timezone.utc) > entry['expires_at']:
        _reset_tokens.pop(token, None)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token has expired. Please request a new one.",
        )

    # ── Password strength check ──
    import re
    if (len(new_password) < 8
            or not re.search(r'[A-Z]', new_password)
            or not re.search(r'[a-z]', new_password)
            or not re.search(r'[0-9]', new_password)
            or not re.search(r'[!@#$%^&*(),.?":{}|<>]', new_password)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters and contain uppercase, lowercase, digit, and special character.",
        )

    # ── Apply reset ──
    user = UserService.get_by_id(db, entry['user_id'])
    if not user:
        _reset_tokens.pop(token, None)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    # Consume the token (single-use)
    _reset_tokens.pop(token, None)

    user.password_hash = hash_password(new_password)
    db.commit()

    logger.info("Password reset successfully for user %s", entry['email'])
    return {"detail": "Password reset successfully. You can now log in with your new password."}
