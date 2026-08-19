"""
SecureTrack Platform — Database Configuration
MySQL engine, session factory, and connection utilities.
"""
import os
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from typing import Generator
import logging

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

# ── Build database URL ──
# Railway sets individual MYSQL* env vars — use those to avoid
# special-character issues in passwords that break URL string parsing.
_mysql_host = os.getenv("MYSQLHOST")
if _mysql_host:
    _db_url = URL.create(
        drivername="mysql+pymysql",
        username=os.getenv("MYSQLUSER", "root"),
        password=os.getenv("MYSQLPASSWORD", ""),
        host=_mysql_host,
        port=int(os.getenv("MYSQLPORT", "3306")),
        database=os.getenv("MYSQLDATABASE", "railway"),
    )
    logger.info(f"✓ Built DATABASE_URL from Railway MYSQL* env vars (host={_mysql_host})")
else:
    # Local dev — use DATABASE_URL from config / .env
    _db_url = settings.DATABASE_URL
    if isinstance(_db_url, str) and _db_url.startswith("mysql://"):
        _db_url = _db_url.replace("mysql://", "mysql+pymysql://", 1)
    logger.info("✓ Using DATABASE_URL from config")

# Build engine kwargs — SQLite doesn't support pooling args
_is_sqlite = str(_db_url).startswith("sqlite")
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

engine = create_engine(_db_url, **_engine_kwargs)

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
