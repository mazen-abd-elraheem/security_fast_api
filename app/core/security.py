"""
SecureTrack Platform — Security Utilities
JWT token creation/verification, password hashing, and token revocation.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid

from jose import jwt, JWTError
from passlib.context import CryptContext
from app.core.config import get_settings

settings = get_settings()

# Bcrypt password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Generate a JWT access token with expiration and unique JTI."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta
        or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({
        "exp": expire,
        "type": "access",
        "jti": str(uuid.uuid4()),  # Unique token ID for revocation
        "iat": datetime.now(timezone.utc),
    })
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(data: dict) -> str:
    """Generate a long-lived JWT refresh token with unique JTI."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({
        "exp": expire,
        "type": "refresh",
        "jti": str(uuid.uuid4()),  # Unique token ID for revocation
        "iat": datetime.now(timezone.utc),
    })
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def verify_token(token: str, expected_type: str = "access") -> Optional[dict]:
    """Decode and verify a JWT token. Returns payload or None."""
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        token_type = payload.get("type", "access")
        if token_type != expected_type:
            return None
        return payload
    except JWTError:
        return None


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check if a plain password matches the hash."""
    return pwd_context.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    """Hash a password for storage."""
    return pwd_context.hash(password)


def is_token_revoked(jti: str, db) -> bool:
    """Check if a token JTI is in the revocation blocklist."""
    from app.models.revoked_token import RevokedToken
    return db.query(RevokedToken).filter(RevokedToken.jti == jti).first() is not None


def revoke_token(jti: str, user_id: str, expires_at: datetime, reason: str, db) -> None:
    """Add a token JTI to the revocation blocklist."""
    from app.models.revoked_token import RevokedToken
    existing = db.query(RevokedToken).filter(RevokedToken.jti == jti).first()
    if not existing:
        revoked = RevokedToken(
            jti=jti,
            user_id=user_id,
            expires_at=expires_at,
            reason=reason,
        )
        db.add(revoked)
        db.commit()


def revoke_all_user_tokens(user_id: str, reason: str, db) -> None:
    """Revoke all tokens for a user by adding a wildcard entry."""
    from app.models.revoked_token import RevokedToken
    wildcard_jti = f"ALL_{user_id}"
    existing = db.query(RevokedToken).filter(RevokedToken.jti == wildcard_jti).first()
    if not existing:
        revoked = RevokedToken(
            jti=wildcard_jti,
            user_id=user_id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS + 1),
            reason=reason,
        )
        db.add(revoked)
        db.commit()


def is_user_tokens_revoked(user_id: str, db) -> bool:
    """Check if ALL tokens for a user have been revoked (wildcard)."""
    from app.models.revoked_token import RevokedToken
    wildcard_jti = f"ALL_{user_id}"
    return db.query(RevokedToken).filter(RevokedToken.jti == wildcard_jti).first() is not None


def cleanup_expired_revocations(db) -> int:
    """Remove expired revocation entries to keep the table small. Returns count deleted."""
    from app.models.revoked_token import RevokedToken
    now = datetime.now(timezone.utc)
    count = db.query(RevokedToken).filter(RevokedToken.expires_at < now).delete()
    db.commit()
    return count
