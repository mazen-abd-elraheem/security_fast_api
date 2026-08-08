"""
SecureTrack Platform — Database Configuration
MySQL engine, session factory, and connection utilities.
"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from typing import Generator
import logging

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

# Build engine kwargs — SQLite doesn't support pooling args
_is_sqlite = settings.DATABASE_URL.startswith("sqlite")
_engine_kwargs = {
    "echo": settings.DEBUG,
}
if _is_sqlite:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update({
        "pool_size": settings.DATABASE_POOL_SIZE,
        "max_overflow": settings.DATABASE_MAX_OVERFLOW,
        "pool_pre_ping": True,
        "pool_recycle": settings.DATABASE_POOL_RECYCLE,
        "pool_timeout": 10,
    })

engine = create_engine(settings.DATABASE_URL, **_engine_kwargs)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

# Base class for all models
Base = declarative_base()

logger.info("✓ SecureTrack database engine created")


# ==========================================
# Dependency Injection
# ==========================================
def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency — yields a DB session, auto-closes."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ==========================================
# Startup Connection Test
# ==========================================
def test_connection():
    """Verify the database is reachable on startup."""
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        logger.info("✓ SecureTrack database connection successful")
    except Exception as e:
        logger.error(f"✗ Database connection failed: {e}")
        raise
