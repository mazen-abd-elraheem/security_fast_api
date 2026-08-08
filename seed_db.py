import sys
import os

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.core.database import SessionLocal, engine, Base
from app.models.user import User
from app.core.security import hash_password
import uuid

def seed():
    # Ensure tables exist
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    # Check if we already have users
    if db.query(User).count() > 0:
        print("Users already exist. Skipping seed.")
        db.close()
        return

    users_to_create = [
        {
            "user_id": str(uuid.uuid4()),
            "name": "Super Admin",
            "email": "admin@securetrack.com",
            "password_hash": hash_password("admin123"),
            "role": "admin",
            "badge_number": "ST-ADMIN-01",
            "is_active": True,
            "status": "active"
        },
        {
            "user_id": str(uuid.uuid4()),
            "name": "John Supervisor",
            "email": "supervisor@securetrack.com",
            "password_hash": hash_password("super123"),
            "role": "supervisor",
            "badge_number": "ST-7729-X",
            "region": "Sector A",
            "is_active": True,
            "status": "active"
        },
        {
            "user_id": str(uuid.uuid4()),
            "name": "Mike Guard",
            "email": "guard@securetrack.com",
            "password_hash": hash_password("guard123"),
            "role": "guard",
            "badge_number": "ST-G-001",
            "region": "Sector A",
            "is_active": True,
            "status": "active"
        }
    ]

    for u in users_to_create:
        db.add(User(**u))
    
    db.commit()
    print("Successfully seeded 3 test users.")
    db.close()

if __name__ == "__main__":
    seed()
