import sys
import os

# Add the project root to sys.path
sys.path.insert(0, os.path.abspath('c:/Users/L0Q/Desktop/security fast api project'))

from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.user import User
from app.api.deps import get_current_user
import uuid

# Override auth dependency for testing
def override_get_current_user():
    db = SessionLocal()
    user = db.query(User).filter(User.role == "supervisor").first()
    db.close()
    if not user:
        # Create one if not exists
        return User(user_id=str(uuid.uuid4()), role="supervisor", name="Test Supervisor")
    return user

app.dependency_overrides[get_current_user] = override_get_current_user

client = TestClient(app)

# We need a valid site_id to test check-in
db = SessionLocal()
from app.models.site import Site
site = db.query(Site).first()
db.close()

if not site:
    print("No sites found in DB")
    sys.exit(1)

print(f"Testing with site: {site.site_id} at {site.latitude}, {site.longitude}")

response = client.post(
    "/api/v1/visits/check-in",
    json={
        "site_id": site.site_id,
        "latitude": site.latitude,
        "longitude": site.longitude,
        "notes": "test"
    }
)
print("Status Code:", response.status_code)
print("Response JSON:", response.json())
