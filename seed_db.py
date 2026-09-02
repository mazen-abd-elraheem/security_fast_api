import sys
import os

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.core.database import SessionLocal, engine, Base
from app.models import *  # Import all models so create_all picks them up
from app.models.user import User
from app.core.security import hash_password
from sqlalchemy import inspect as sa_inspect, text as sa_text
import uuid


def _run_seed_migrations():
    try:
        insp = sa_inspect(engine)
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
                "status": "VARCHAR(30) NOT NULL DEFAULT 'pending'",
                "fcm_token": "VARCHAR(500) NULL",
                "payroll_amount": "FLOAT DEFAULT 0",
        "shift_type": "VARCHAR(10) NULL",
                "transfer_name": "VARCHAR(255) NULL",
                "transfer_method": "VARCHAR(100) NULL",
                "uniform_status": "VARCHAR(100) DEFAULT 'none'",
            }
            with engine.begin() as conn:
                for col_name, col_def in new_cols.items():
                    if col_name not in existing:
                        try:
                            conn.execute(sa_text(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"))
                            print(f"Added '{col_name}' to users")
                        except Exception as col_err:
                            print(f"Skipped '{col_name}': {col_err}")

        if insp.has_table("guard_documents"):
            existing = {c["name"] for c in insp.get_columns("guard_documents")}
            if "expiry_date" not in existing:
                with engine.begin() as conn:
                    try:
                        conn.execute(sa_text("ALTER TABLE guard_documents ADD COLUMN expiry_date DATE NULL"))
                        print("Added 'expiry_date' to guard_documents")
                    except Exception as col_err:
                        print(f"Skipped 'expiry_date': {col_err}")

        if insp.has_table("attendance_logs"):
            existing = {c["name"] for c in insp.get_columns("attendance_logs")}
            if "total_outside_seconds" not in existing:
                with engine.begin() as conn:
                    conn.execute(sa_text("ALTER TABLE attendance_logs ADD COLUMN total_outside_seconds FLOAT NOT NULL DEFAULT 0"))
                    print("Added 'total_outside_seconds' to attendance_logs")
    except Exception as e:
        print(f"Seed migration check: {e}")

    if insp.has_table("cash_advances"):
        existing = {c["name"] for c in insp.get_columns("cash_advances")}
        ca_new = {
            "ops_manager_id": "VARCHAR(36) NULL",
            "ops_manager_notes": "TEXT NULL",
            "ops_reviewed_at": "DATETIME NULL",
            "ceo_id": "VARCHAR(36) NULL",
            "ceo_notes": "TEXT NULL",
            "ceo_reviewed_at": "DATETIME NULL",
        }
        for col_name, col_def in ca_new.items():
            if col_name not in existing:
                with engine.begin() as conn:
                    conn.execute(sa_text(f"ALTER TABLE cash_advances ADD COLUMN {col_name} {col_def}"))
                    print(f"  [migration] cash_advances.{col_name} added")

    if insp.has_table("sites"):
        existing = {c["name"] for c in insp.get_columns("sites")}
        if "is_base" not in existing:
            with engine.begin() as conn:
                conn.execute(sa_text("ALTER TABLE sites ADD COLUMN is_base BOOLEAN NOT NULL DEFAULT 0"))
                print("  [migration] sites.is_base added")

    if insp.has_table("monthly_payroll"):
        existing = {c["name"] for c in insp.get_columns("monthly_payroll")}
        if "travel_allowance" not in existing:
            with engine.begin() as conn:
                conn.execute(sa_text("ALTER TABLE monthly_payroll ADD COLUMN travel_allowance FLOAT NOT NULL DEFAULT 0"))
                print("  [migration] monthly_payroll.travel_allowance added")

def seed():
    Base.metadata.create_all(bind=engine)
    _run_seed_migrations()

    db = SessionLocal()
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

    # -- attendance_logs new columns --
    from sqlalchemy import inspect as sa_inspect, text as sa_text
    insp = sa_inspect(engine)
    if insp.has_table("attendance_logs"):
        existing_att = {c["name"] for c in insp.get_columns("attendance_logs")}
        att_new_cols = {
            "absence_type": "VARCHAR(20) NULL",
            "excused_by": "VARCHAR(100) NULL",
            "overtime_hours": "FLOAT DEFAULT 0",
            "overtime_approved_by": "VARCHAR(100) NULL",
            "overtime_approved": "BOOLEAN DEFAULT FALSE",
            "is_rest_day": "BOOLEAN DEFAULT FALSE",
            "is_sick_leave": "BOOLEAN DEFAULT FALSE",
            "is_annual_leave": "BOOLEAN DEFAULT FALSE",
        }
        for col_name, col_def in att_new_cols.items():
            if col_name not in existing_att:
                with engine.begin() as conn:
                    conn.execute(sa_text(f"ALTER TABLE attendance_logs ADD COLUMN {col_name} {col_def}"))
                    print(f"  Added attendance_logs.{col_name}")

    if insp.has_table("daily_attendance_entries"):
        existing_dae = {c["name"] for c in insp.get_columns("daily_attendance_entries")}
        if "advance_amount" not in existing_dae:
            with engine.begin() as conn:
                conn.execute(sa_text("ALTER TABLE daily_attendance_entries ADD COLUMN advance_amount FLOAT DEFAULT 0"))
                print("  Added daily_attendance_entries.advance_amount")
