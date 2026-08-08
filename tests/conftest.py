"""
SecureTrack Platform — Test Configuration
Fixtures for all test modules: in-memory DB, test client, user factories.
"""
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import hash_password, create_access_token
from app.main import app
from app.models.user import User
from app.models.site import Site
from app.models.shift import Shift
from app.models.guard_roster import GuardRoster
from app.models.supervisor_route import SupervisorRoute


# ==========================================
# In-Memory SQLite for Testing
# ==========================================
TEST_DATABASE_URL = "sqlite://"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_database():
    """Create all tables before each test, drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    """Provide a database session for tests."""
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    """Provide a test HTTP client."""
    return TestClient(app)


# ==========================================
# User Factory Helpers
# ==========================================
def _create_user(db, role: str, name: str = None, email: str = None, badge: str = None):
    """Create a test user with the given role."""
    user_id = str(uuid.uuid4())
    user = User(
        user_id=user_id,
        name=name or f"Test {role.title()}",
        email=email or f"{role}_{user_id[:8]}@test.com",
        password_hash=hash_password("Test@1234"),
        role=role,
        badge_number=badge,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _get_token(user: User) -> str:
    """Generate a JWT token for a user."""
    token_data = {"sub": user.user_id, "role": user.role}
    return create_access_token(data=token_data)


def _auth_header(user: User) -> dict:
    """Generate auth header for a user."""
    return {"Authorization": f"Bearer {_get_token(user)}"}


@pytest.fixture
def admin_user(db):
    return _create_user(db, "admin", "Admin User", "admin@test.com")


@pytest.fixture
def supervisor_user(db):
    return _create_user(db, "supervisor", "Supervisor User", "supervisor@test.com")


@pytest.fixture
def guard_user(db):
    return _create_user(db, "guard", "Guard User", "guard@test.com", badge="G-001")


@pytest.fixture
def ops_manager_user(db):
    return _create_user(db, "operations_manager", "Ops Manager", "ops@test.com")


@pytest.fixture
def client_user(db):
    return _create_user(db, "client", "Client User", "client@test.com")


@pytest.fixture
def admin_headers(admin_user):
    return _auth_header(admin_user)


@pytest.fixture
def supervisor_headers(supervisor_user):
    return _auth_header(supervisor_user)


@pytest.fixture
def guard_headers(guard_user):
    return _auth_header(guard_user)


@pytest.fixture
def ops_headers(ops_manager_user):
    return _auth_header(ops_manager_user)


# ==========================================
# Domain Object Factories
# ==========================================
@pytest.fixture
def test_site(db):
    """Create a test site with geofence."""
    site = Site(
        site_id=str(uuid.uuid4()),
        name="Test Site Alpha",
        address="123 Security St",
        latitude=30.0444,   # Cairo coordinates
        longitude=31.2357,
        radius_meters=100,
        region="Cairo",
        status="active",
    )
    db.add(site)
    db.commit()
    db.refresh(site)
    return site


@pytest.fixture
def test_shift(db, test_site):
    """Create a test shift at the test site."""
    from datetime import time
    shift = Shift(
        shift_id=str(uuid.uuid4()),
        site_id=test_site.site_id,
        start_time=time(8, 0),
        end_time=time(16, 0),
        days_of_week="mon,tue,wed,thu,fri",
        required_headcount=2,
        label="Morning Shift",
    )
    db.add(shift)
    db.commit()
    db.refresh(shift)
    return shift


@pytest.fixture
def test_roster(db, guard_user, test_shift):
    """Create a test roster assignment."""
    from datetime import date
    roster = GuardRoster(
        roster_id=str(uuid.uuid4()),
        guard_id=guard_user.user_id,
        shift_id=test_shift.shift_id,
        assigned_date=date.today(),
    )
    db.add(roster)
    db.commit()
    db.refresh(roster)
    return roster


@pytest.fixture
def test_route(db, supervisor_user, test_site):
    """Create a test route assignment."""
    from datetime import date
    route = SupervisorRoute(
        route_id=str(uuid.uuid4()),
        supervisor_id=supervisor_user.user_id,
        site_id=test_site.site_id,
        assigned_date=date.today(),
        visit_order=1,
    )
    db.add(route)
    db.commit()
    db.refresh(route)
    return route
