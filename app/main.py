"""
SecureTrack Platform Ã¢â‚¬â€ Application Entry Point
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
    deduction_rules, fake_attendance, uniforms, cash_advance,
    inventory, personnel, complaints, leave_requests,
    daily_logbook, separations, payroll_engine,
    operations_room,
    disciplinary,
    evaluations,
    accountant,
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
    Each migration is idempotent Ã¢â‚¬â€ safe to run on every startup.
    """
    from sqlalchemy import inspect as sa_inspect, text as sa_text

    try:
        insp = sa_inspect(engine)

        # Ã¢â€â‚¬Ã¢â€â‚¬ attendance_logs.total_outside_seconds Ã¢â€â‚¬Ã¢â€â‚¬
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
                logger.info("Ã¢Å“â€œ Migration: added 'total_outside_seconds' to attendance_logs")
            else:
                logger.info("Ã¢Å“â€œ Migration: 'total_outside_seconds' already exists Ã¢â‚¬â€ skipped")

        # Ã¢â€â‚¬Ã¢â€â‚¬ users: payroll + HR columns Ã¢â€â‚¬Ã¢â€â‚¬
        if insp.has_table("users"):
            existing = {c["name"] for c in insp.get_columns("users")}
            new_cols = {
                "employee_code": "VARCHAR(50) NULL",
                "region": "VARCHAR(100) NULL",
                "requested_role": "VARCHAR(30) NULL",
                "base_salary": "FLOAT DEFAULT 0",
                "daily_rate": "FLOAT DEFAULT 0",
                "classification": "VARCHAR(50) NULL",
                "hire_date": "DATETIME NULL",
                "insurance_status": "VARCHAR(30) DEFAULT 'none'",
                "bank_account": "VARCHAR(100) NULL",
                "fcm_token": "VARCHAR(500) NULL",
                "payroll_amount": "FLOAT DEFAULT 0",
            }
            with engine.begin() as conn:
                for col_name, col_def in new_cols.items():
                    if col_name not in existing:
                        conn.execute(sa_text(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"))
                        logger.info(f"Ã¢Å“â€œ Migration: added '{col_name}' to users")


        # guard_documents.expiry_date
        if insp.has_table("guard_documents"):
            existing = {c["name"] for c in insp.get_columns("guard_documents")}
            if "expiry_date" not in existing:
                with engine.begin() as conn:
                    try:
                        conn.execute(sa_text("ALTER TABLE guard_documents ADD COLUMN expiry_date DATE NULL"))
                        logger.info("Added expiry_date to guard_documents")
                    except Exception:
                        pass

    except Exception as e:
        logger.warning(f"Ã¢Å¡Â  Auto-migration check failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info(f"Ã°Å¸â€ºÂ¡Ã¯Â¸Â Starting {settings.APP_NAME}")

    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Ã¢Å“â€œ Database tables created/verified")

        # Add any missing columns to existing tables
        _run_auto_migrations()

        # Create SQL views for optimized dashboard queries
        db = SessionLocal()
        try:
            create_views(db)
            logger.info("Ã¢Å“â€œ SQL views created/updated")
        finally:
            db.close()

        test_connection()
    except Exception as e:
        logger.warning(f"Ã¢Å¡Â  MySQL not available on startup: {e}")
        logger.warning("  Ã¢â€ â€™ Start MySQL and the app will connect on first request.")

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    logger.info(f"Ã¢Å“â€œ Upload directory ready: {settings.UPLOAD_DIR}")

    yield

    logger.info("Ã°Å¸â€ºâ€˜ Shutting down...")
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
            "%s %s Ã¢â€ â€™ %s (%.2fs)",
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
        db_status = "disconnected Ã¢â‚¬â€ start MySQL with: net start MySQL80"
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
app.include_router(uniforms.router, prefix="/api/v1/uniforms", tags=["Uniforms"])
app.include_router(cash_advance.router, prefix="/api/v1/cash-advance", tags=["Cash Advance"])
app.include_router(inventory.router, prefix="/api/v1/inventory", tags=["Inventory"])
app.include_router(personnel.router, prefix="/api/v1/personnel", tags=["Personnel"])
app.include_router(complaints.router, prefix="/api/v1/complaints", tags=["Complaints"])
app.include_router(leave_requests.router, prefix="/api/v1/leave-requests", tags=["Leave Requests"])
app.include_router(daily_logbook.router, prefix="/api/v1/logbook", tags=["Daily Logbook"])
app.include_router(separations.router, prefix="/api/v1/separations", tags=["Separations"])
app.include_router(payroll_engine.router, prefix="/api/v1/payroll-engine", tags=["Payroll Engine"])
app.include_router(operations_room.router, prefix="/api/v1/operations-room", tags=["Operations Room"])
app.include_router(disciplinary.router, prefix="/api/v1/disciplinary", tags=["Disciplinary Actions"])
app.include_router(evaluations.router, prefix="/api/v1/evaluations", tags=["Guard Evaluations"])
app.include_router(accountant.router, prefix="/api/v1/accountant-sheet", tags=["Accountant Sheet"])


# ==========================================
# Static Files (uploaded images) Ã¢â‚¬â€ must be AFTER routers
# ==========================================
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount(
    "/static/uploads",
    StaticFiles(directory=settings.UPLOAD_DIR),
    name="uploads",
)

logger.info(f"Ã°Å¸â€ºÂ¡Ã¯Â¸Â {settings.APP_NAME} v1.0.0 Ã¢â‚¬â€ all routers registered")
