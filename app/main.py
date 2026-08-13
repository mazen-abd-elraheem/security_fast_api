"""
SecureTrack Platform — Application Entry Point
FastAPI app with lifespan, middleware, global exception handler, and router registration.
"""
import os
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.core.config import get_settings
from app.core.database import engine, SessionLocal, test_connection
from app.core.views import create_views
from app.core.exceptions import (
    SecureTrackException,
    NotFoundException,
    DuplicateException,
    ForbiddenException,
    BadRequestException,
    UnauthorizedException,
    GeofenceViolationException,
    DeviceNotTrustedException,
    OfflineSyncExpiredException,
)
from app.models import Base
from app.api.v1 import (
    auth, users, sites, shifts, roster, routes,
    visits, attendance, incidents, dashboard,
    notifications, admin, password_reset, sync, devices,
    guard_photos, outdoor, workforce, tracking, payroll,
    deduction_rules, fake_attendance,
)

settings = get_settings()
logger = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)


# ==========================================
# Application Lifespan
# ==========================================
def _run_auto_migrations():
    """
    Add missing columns that create_all won't handle on existing tables.
    Each migration is idempotent — safe to run on every startup.
    """
    from sqlalchemy import inspect as sa_inspect, text as sa_text

    try:
        insp = sa_inspect(engine)

        # ── attendance_logs.total_outside_seconds ──
        if insp.has_table("attendance_logs"):
            existing = {c["name"] for c in insp.get_columns("attendance_logs")}
            if "total_outside_seconds" not in existing:
                with engine.begin() as conn:
                    dialect = engine.dialect.name
                    if dialect == "sqlite":
                        conn.execute(sa_text(
                            "ALTER TABLE attendance_logs ADD COLUMN total_outside_seconds FLOAT NOT NULL DEFAULT 0"
                        ))
                    else:
                        conn.execute(sa_text(
                            "ALTER TABLE attendance_logs ADD COLUMN total_outside_seconds FLOAT NOT NULL DEFAULT 0"
                        ))
                logger.info("✓ Migration: added 'total_outside_seconds' to attendance_logs")
            else:
                logger.info("✓ Migration: 'total_outside_seconds' already exists — skipped")
    except Exception as e:
        logger.warning(f"⚠ Auto-migration check failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info(f"🛡️ Starting {settings.APP_NAME}")

    try:
        Base.metadata.create_all(bind=engine)
        logger.info("✓ Database tables created/verified")

        # Add any missing columns to existing tables
        _run_auto_migrations()

        # Create SQL views for optimized dashboard queries
        db = SessionLocal()
        try:
            create_views(db)
            logger.info("✓ SQL views created/updated")
        finally:
            db.close()

        test_connection()
    except Exception as e:
        logger.warning(f"⚠ MySQL not available on startup: {e}")
        logger.warning("  → Start MySQL and the app will connect on first request.")

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    logger.info(f"✓ Upload directory ready: {settings.UPLOAD_DIR}")

    yield

    logger.info("🛑 Shutting down...")
    engine.dispose()


# ==========================================
# FastAPI Application
# ==========================================
app = FastAPI(
    title=settings.APP_NAME,
    description="Security Field Force Management System. "
                "GPS-verified supervisor visits, guard attendance tracking, "
                "geofenced check-ins, and real-time compliance monitoring.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


# ==========================================
# Global Exception Handlers
# ==========================================
@app.exception_handler(SecureTrackException)
async def securetrack_exception_handler(request: Request, exc: SecureTrackException):
    """Convert domain exceptions to proper HTTP responses."""
    status_map = {
        GeofenceViolationException: 403,
        DeviceNotTrustedException: 403,
        OfflineSyncExpiredException: 410,
        NotFoundException: 404,
        DuplicateException: 409,
        ForbiddenException: 403,
        BadRequestException: 400,
        UnauthorizedException: 401,
    }
    status_code = status_map.get(type(exc), 500)
    return JSONResponse(status_code=status_code, content={"detail": exc.message})


# ==========================================
# Rate Limiting
# ==========================================
limiter = Limiter(key_func=get_remote_address, default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ==========================================
# Middleware
# ==========================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================
# Request Logging Middleware
# ==========================================
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every request with method, path, duration, and status."""
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time

    if not request.url.path.startswith("/static"):
        logger.info(
            "%s %s → %s (%.2fs)",
            request.method,
            request.url.path,
            response.status_code,
            duration,
        )
    else:
        response.headers["Cache-Control"] = "public, max-age=2592000, immutable"
    return response


# ==========================================
# Root Redirect
# ==========================================
@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")


# ==========================================
# Health Check
# ==========================================
@app.get("/health", tags=["Health"])
async def health_check():
    db_status = "unknown"
    try:
        test_connection()
        db_status = "connected"
    except Exception:
        db_status = "disconnected — start MySQL with: net start MySQL80"
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": "1.0.0",
        "database": db_status,
    }


# ==========================================
# Register Routers
# ==========================================
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(users.router, prefix="/api/v1/users", tags=["Users"])
app.include_router(sites.router, prefix="/api/v1/sites", tags=["Sites"])
app.include_router(shifts.router, prefix="/api/v1/sites", tags=["Shifts"])
app.include_router(roster.router, prefix="/api/v1/roster", tags=["Guard Roster"])
app.include_router(routes.router, prefix="/api/v1/routes", tags=["Supervisor Routes"])
app.include_router(visits.router, prefix="/api/v1/visits", tags=["Visits (Geofence)"])
app.include_router(attendance.router, prefix="/api/v1/attendance", tags=["Attendance"])
app.include_router(incidents.router, prefix="/api/v1/incidents", tags=["Incidents"])
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["Dashboard"])
app.include_router(notifications.router, prefix="/api/v1/notifications", tags=["Notifications"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(password_reset.router, prefix="/api/v1/auth/password", tags=["Password Reset"])
app.include_router(sync.router, prefix="/api/v1/sync", tags=["Offline Sync"])
app.include_router(devices.router, prefix="/api/v1/devices", tags=["Devices"])
app.include_router(guard_photos.router, prefix="/api/v1/guard-photos", tags=["Guard Photos"])
app.include_router(outdoor.router, prefix="/api/v1/outdoor", tags=["Outdoor"])
app.include_router(workforce.router, prefix="/api/v1/workforce", tags=["Workforce Log"])
app.include_router(tracking.router, prefix="/api/v1/tracking", tags=["GPS Tracking"])
app.include_router(payroll.router, prefix="/api/v1/payroll", tags=["Payroll"])
app.include_router(deduction_rules.router, prefix="/api/v1/deductions", tags=["Deduction Rules"])
app.include_router(fake_attendance.router, prefix="/api/v1/fake-attendance", tags=["Fake Attendance"])


# ==========================================
# Static Files (uploaded images) — must be AFTER routers
# ==========================================
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount(
    "/static/uploads",
    StaticFiles(directory=settings.UPLOAD_DIR),
    name="uploads",
)

logger.info(f"🛡️ {settings.APP_NAME} v1.0.0 — all routers registered")
