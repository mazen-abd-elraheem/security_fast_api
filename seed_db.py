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
                # Security — Account Lockout
                "failed_login_count": "INTEGER DEFAULT 0",
                "locked_until": "DATETIME NULL",
                "last_failed_login": "DATETIME NULL",
                "national_id": "VARCHAR(20) NULL",
                "insurance_number": "VARCHAR(50) NULL",
                "insurance_date": "DATETIME NULL",
                "insurable_wage": "FLOAT DEFAULT 0",
                # Security — MFA (TOTP)
                "totp_secret": "VARCHAR(32) NULL",
                "totp_enabled": "BOOLEAN DEFAULT FALSE",
                "totp_confirmed_at": "DATETIME NULL",
                # Employee Documents Sheet
                "file_number": "VARCHAR(100) NULL",
                "documents_notes": "VARCHAR(500) NULL",
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

    # ── travel_allowance_entries: ensure table columns are up to date ──
    try:
        if insp.has_table("travel_allowance_entries"):
            existing = {c["name"] for c in insp.get_columns("travel_allowance_entries")}
            tae_cols = {
                "is_active": "BOOLEAN DEFAULT TRUE",
            }
            for col_name, col_def in tae_cols.items():
                if col_name not in existing:
                    with engine.begin() as conn:
                        conn.execute(sa_text(f"ALTER TABLE travel_allowance_entries ADD COLUMN {col_name} {col_def}"))
                        print(f"  [migration] travel_allowance_entries.{col_name} added")
    except Exception as e:
        print(f"travel_allowance_entries migration: {e}")

    # ── Cash Advances table: add ops_manager and CEO columns ──
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

    # ── rest_allowance_config: table is created by create_all, no extra columns needed ──
    # Just log its existence
    if insp.has_table("rest_allowance_config"):
        print("  [migration] rest_allowance_config table exists")
    else:
        print("  [migration] rest_allowance_config table will be created by create_all")

    # ── Clothes Requests: add workflow columns ──
    if insp.has_table("clothes_requests"):
        existing = {c["name"] for c in insp.get_columns("clothes_requests")}
        cr_new = {
            "status": "VARCHAR(50) DEFAULT 'pending_ops'",
            "ops_manager_id": "VARCHAR(36) NULL",
            "ops_reason": "VARCHAR(255) NULL",
            "hr_manager_id": "VARCHAR(36) NULL",
            "hr_reason": "VARCHAR(255) NULL",
        }
        for col_name, col_def in cr_new.items():
            if col_name not in existing:
                with engine.begin() as conn:
                    conn.execute(sa_text(f"ALTER TABLE clothes_requests ADD COLUMN {col_name} {col_def}"))
                    print(f"  [migration] clothes_requests.{col_name} added")
        if "user_id" not in existing:
            with engine.begin() as conn:
                conn.execute(sa_text("ALTER TABLE clothes_requests ADD COLUMN user_id VARCHAR(36) NULL"))
                print("  [migration] clothes_requests.user_id added")

    # ── Deduction Rules: add notice period and days multiplier ──
    if insp.has_table("deduction_rules"):
        existing = {c["name"] for c in insp.get_columns("deduction_rules")}
        dr_new = {
            "is_days_multiplier": "BOOLEAN DEFAULT FALSE",
            "notice_period_days": "INTEGER DEFAULT 0",
        }
        for col_name, col_def in dr_new.items():
            if col_name not in existing:
                with engine.begin() as conn:
                    conn.execute(sa_text(f"ALTER TABLE deduction_rules ADD COLUMN {col_name} {col_def}"))
                    print(f"  [migration] deduction_rules.{col_name} added")

    # ── Separation Requests: add workflow dates ──
    if insp.has_table("separation_requests"):
        existing = {c["name"] for c in insp.get_columns("separation_requests")}
        sr_new = {
            "requested_last_working_day": "DATETIME NULL",
            "actual_last_working_day": "DATETIME NULL",
        }
        for col_name, col_def in sr_new.items():
            if col_name not in existing:
                with engine.begin() as conn:
                    conn.execute(sa_text(f"ALTER TABLE separation_requests ADD COLUMN {col_name} {col_def}"))
                    print(f"  [migration] separation_requests.{col_name} added")

    # ── Clothes Terminations: add workflow columns ──
    if insp.has_table("clothes_terminations"):
        existing = {c["name"] for c in insp.get_columns("clothes_terminations")}
        if "user_id" not in existing:
            with engine.begin() as conn:
                conn.execute(sa_text("ALTER TABLE clothes_terminations ADD COLUMN user_id VARCHAR(36) NULL"))
                print("  [migration] clothes_terminations.user_id added")
        if "calculated_deduction" not in existing:
            with engine.begin() as conn:
                conn.execute(sa_text("ALTER TABLE clothes_terminations ADD COLUMN calculated_deduction FLOAT DEFAULT 0"))
                print("  [migration] clothes_terminations.calculated_deduction added")

    # ── Inventory Items: add replacement cost ──
    if insp.has_table("inventory_items"):
        existing = {c["name"] for c in insp.get_columns("inventory_items")}
        if "replacement_cost" not in existing:
            with engine.begin() as conn:
                conn.execute(sa_text("ALTER TABLE inventory_items ADD COLUMN replacement_cost FLOAT DEFAULT 0"))
                print("  [migration] inventory_items.replacement_cost added")

    # ── Client Accounts: add site_id ──
    if insp.has_table("client_accounts"):
        existing = {c["name"] for c in insp.get_columns("client_accounts")}
        if "site_id" not in existing:
            with engine.begin() as conn:
                conn.execute(sa_text("ALTER TABLE client_accounts ADD COLUMN site_id VARCHAR(36) NULL"))
                print("  [migration] client_accounts.site_id added")

    # ══════════════════════════════════════════════
    # Task Inspection & Alert System tables
    # ══════════════════════════════════════════════
    # These are NEW tables — Base.metadata.create_all() handles initial creation.
    # This block ensures future column additions are handled idempotently.
    task_tables = [
        "tenants", "tenant_site_access", "client_accounts",
        "task_roles", "task_role_assignments",
        "task_templates", "task_sections", "task_items", "task_item_alert_recipients",
        "task_instances", "task_responses",
        "task_alerts", "task_alert_deliveries",
        "task_instance_comments",
    ]
    for tbl in task_tables:
        if insp.has_table(tbl):
            print(f"  [migration] {tbl} table exists ✓")
        else:
            print(f"  [migration] {tbl} will be created by create_all")

    # ── Tenants: add tenant_code column ──
    if insp.has_table("tenants"):
        existing = {c["name"] for c in insp.get_columns("tenants")}
        if "tenant_code" not in existing:
            with engine.begin() as conn:
                conn.execute(sa_text("ALTER TABLE tenants ADD COLUMN tenant_code VARCHAR(20) NULL"))
                print("  [migration] tenants.tenant_code added")
                # Back-fill existing tenants with auto-generated codes
                from app.models.task_models import Tenant
                rows = conn.execute(sa_text("SELECT tenant_id FROM tenants WHERE tenant_code IS NULL")).fetchall()
                import random, string
                for row in rows:
                    code = 'T-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
                    conn.execute(sa_text(f"UPDATE tenants SET tenant_code = :code WHERE tenant_id = :tid"),
                                 {"code": code, "tid": row[0]})
                    print(f"  [migration] tenants back-filled code {code} for {row[0]}")



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
            "name": "CEO Executive",
            "email": "ceo@securetrack.com",
            "password_hash": hash_password("ceo123"),
            "role": "ceo",
            "badge_number": "ST-CEO-01",
            "is_active": True,
            "status": "active"
        },
        {
            "user_id": str(uuid.uuid4()),
            "name": "John Supervisor",
            "email": "supervisor@securetrack.com",
            "password_hash": hash_password("supervisor123"),
            "role": "supervisor",
            "badge_number": "ST-7729-X",
            "region": "Sector A",
            "is_active": True,
            "status": "active"
        },
        {
            "user_id": str(uuid.uuid4()),
            "name": "Team Leader",
            "email": "leader@securetrack.com",
            "password_hash": hash_password("leader123"),
            "role": "leader",
            "badge_number": "ST-L-01",
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
        },
        {
            "user_id": str(uuid.uuid4()),
            "name": "Outdoor Agent",
            "email": "outdoor@securetrack.com",
            "password_hash": hash_password("outdoor123"),
            "role": "outdoor",
            "badge_number": "ST-O-001",
            "is_active": True,
            "status": "active"
        },
        {
            "user_id": str(uuid.uuid4()),
            "name": "Lady Guard",
            "email": "lady@securetrack.com",
            "password_hash": hash_password("lady123"),
            "role": "lady",
            "badge_number": "ST-LY-001",
            "is_active": True,
            "status": "active"
        },
        {
            "user_id": str(uuid.uuid4()),
            "name": "Personnel Officer",
            "email": "personnel@securetrack.com",
            "password_hash": hash_password("personnel123"),
            "role": "personnel_officer",
            "badge_number": "ST-PO-001",
            "is_active": True,
            "status": "active"
        },
        {
            "user_id": str(uuid.uuid4()),
            "name": "HR Manager",
            "email": "hr@securetrack.com",
            "password_hash": hash_password("hr123"),
            "role": "hr",
            "badge_number": "ST-HR-001",
            "is_active": True,
            "status": "active"
        },
        {
            "user_id": str(uuid.uuid4()),
            "name": "Senior Accountant",
            "email": "accountant@securetrack.com",
            "password_hash": hash_password("accountant123"),
            "role": "accountant",
            "badge_number": "ST-ACC-001",
            "is_active": True,
            "status": "active"
        },
        {
            "user_id": str(uuid.uuid4()),
            "name": "Operations Manager",
            "email": "ops@securetrack.com",
            "password_hash": hash_password("ops123"),
            "role": "operations_manager",
            "badge_number": "ST-OPS-001",
            "is_active": True,
            "status": "active"
        }
    ]

    for u in users_to_create:
        db.add(User(**u))

    db.commit()
    print(f"Successfully seeded {len(users_to_create)} test users.")
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
