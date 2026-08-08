"""Migration script: Add status and requested_role columns to users table."""
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.core.database import SessionLocal, engine
from sqlalchemy import text

db = SessionLocal()

# Add columns
try:
    db.execute(text("ALTER TABLE users ADD COLUMN status VARCHAR(30) NOT NULL DEFAULT 'active'"))
    print("Added 'status' column")
except Exception as e:
    print(f"status column may already exist: {e}")

try:
    db.execute(text("ALTER TABLE users ADD COLUMN requested_role VARCHAR(30)"))
    print("Added 'requested_role' column")
except Exception as e:
    print(f"requested_role column may already exist: {e}")

# Ensure all existing users have status=active
db.execute(text("UPDATE users SET status = 'active' WHERE status IS NULL OR status = ''"))
db.commit()
print("Ensured all existing users have status='active'")

db.close()
print("Migration complete!")
