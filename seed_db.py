import sys
import os

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.core.database import SessionLocal, engine, Base
from app.models import *  # Import all models so create_all picks them up
from app.models.user import User
from app.models.payroll_formula_config import PayrollFormulaConfig, DEFAULT_FORMULA_SEED
from app.core.security import hash_password
from sqlalchemy import inspect as sa_inspect, text as sa_text
import uuid
from datetime import datetime, timezone



def _run_seed_migrations():
    try:
        insp = sa_inspect(engine)
        if insp.has_table("users"):
            existing = {c["name"] for c in insp.get_columns("users")}
            new_cols = {
                "tenant_id": "VARCHAR(36) NULL",
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

    # ── Task Templates: add schedule_id & requires_qr_verification ──
    if insp.has_table("task_templates"):
        existing = {c["name"] for c in insp.get_columns("task_templates")}
        if "schedule_id" not in existing:
            with engine.begin() as conn:
                conn.execute(sa_text("ALTER TABLE task_templates ADD COLUMN schedule_id VARCHAR(36) NULL"))
                print("  [migration] task_templates.schedule_id added")

    # ── Task Items: add requires_qr_verification ──
    if insp.has_table("task_items"):
        existing = {c["name"] for c in insp.get_columns("task_items")}
        if "requires_qr_verification" not in existing:
            with engine.begin() as conn:
                conn.execute(sa_text("ALTER TABLE task_items ADD COLUMN requires_qr_verification BOOLEAN DEFAULT FALSE"))
                print("  [migration] task_items.requires_qr_verification added")

    # ── Payroll Sheet Rows: expand shift_time ──
    if insp.has_table("payroll_sheet_rows"):
        with engine.begin() as conn:
            try:
                conn.execute(sa_text("ALTER TABLE payroll_sheet_rows MODIFY COLUMN shift_time VARCHAR(50)"))
                print("  [migration] payroll_sheet_rows.shift_time expanded to VARCHAR(50)")
            except Exception as e:
                print(f"  [migration] Error expanding payroll_sheet_rows.shift_time: {e}")

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

    # ── Client Accounts: add security/lockout fields ──
    if insp.has_table("client_accounts"):
        existing = {c["name"] for c in insp.get_columns("client_accounts")}
        client_new = {
            "failed_login_attempts": "INTEGER DEFAULT 0",
            "locked_until": "DATETIME NULL",
            "totp_secret": "VARCHAR(32) NULL",
            "totp_enabled": "BOOLEAN DEFAULT FALSE",
        }
        for col_name, col_def in client_new.items():
            if col_name not in existing:
                with engine.begin() as conn:
                    conn.execute(sa_text(f"ALTER TABLE client_accounts ADD COLUMN {col_name} {col_def}"))
                    print(f"  [migration] client_accounts.{col_name} added")

    # ── Sites: add tactical metrics ──
    if insp.has_table("sites"):
        existing = {c["name"] for c in insp.get_columns("sites")}
        site_new = {
            "defcon_level": "INTEGER DEFAULT 4",
            "clearance_level": "VARCHAR(50) DEFAULT 'L3 Active'",
            "last_audit_timestamp": "DATETIME NULL",
            "perimeter_fill_rate_trend": "FLOAT DEFAULT 0.0",
            "breach_response_readiness_seconds": "INTEGER DEFAULT 102",
            "unassigned_standby_pool": "INTEGER DEFAULT 0",
        }
        for col_name, col_def in site_new.items():
            if col_name not in existing:
                with engine.begin() as conn:
                    conn.execute(sa_text(f"ALTER TABLE sites ADD COLUMN {col_name} {col_def}"))
                    print(f"  [migration] sites.{col_name} added")

    # ── Shifts: add tactical details ──
    if insp.has_table("shifts"):
        existing = {c["name"] for c in insp.get_columns("shifts")}
        shift_new = {
            "location_tag": "VARCHAR(100) NULL",
            "checkpoints_scheduled": "INTEGER DEFAULT 0",
            "compliance_gauge": "FLOAT DEFAULT 100.0",
            "armed_standard": "VARCHAR(100) NULL",
            "rfid_perimeter_status": "VARCHAR(100) NULL",
            "vehicles_assigned": "INTEGER DEFAULT 0",
            "sector_loops": "INTEGER DEFAULT 0",
            "authorization_protocol": "VARCHAR(100) NULL",
            "compliance_certification": "VARCHAR(100) NULL",
            "shift_status_override": "VARCHAR(50) NULL",
        }
        for col_name, col_def in shift_new.items():
            if col_name not in existing:
                with engine.begin() as conn:
                    conn.execute(sa_text(f"ALTER TABLE shifts ADD COLUMN {col_name} {col_def}"))
                    print(f"  [migration] shifts.{col_name} added")

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
        "task_schedules", "task_templates", "task_sections", "task_items", "task_item_alert_recipients",
        "task_instances", "task_responses",
        "task_alerts", "task_alert_deliveries",
        "task_instance_comments",
    ]
    for tbl in task_tables:
        if insp.has_table(tbl):
            print(f"  [migration] {tbl} table exists OK")
        else:
            print(f"  [migration] {tbl} will be created by create_all")

    if insp.has_table("task_instances"):
        existing = {c["name"] for c in insp.get_columns("task_instances")}
        with engine.begin() as conn:
            if "schedule_id" not in existing:
                conn.execute(sa_text("ALTER TABLE task_instances ADD COLUMN schedule_id VARCHAR(36) NULL"))
                print("  [migration] task_instances.schedule_id added")
            if "section_id" not in existing:
                conn.execute(sa_text("ALTER TABLE task_instances ADD COLUMN section_id VARCHAR(36) NULL"))
                print("  [migration] task_instances.section_id added")
            
            # Also modify assigned_to to allow NULL
            # MySQL syntax for modifying column
            try:
                conn.execute(sa_text("ALTER TABLE task_instances MODIFY assigned_to VARCHAR(36) NULL"))
                print("  [migration] task_instances.assigned_to made nullable")
            except Exception as e:
                print(f"  [migration] failed to make assigned_to nullable: {e}")

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



    # -- attendance_logs new columns --
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

    if insp.has_table("supervisor_routes"):
        existing = {c["name"] for c in insp.get_columns("supervisor_routes")}
        if "shift_id" not in existing:
            with engine.begin() as conn:
                conn.execute(sa_text("ALTER TABLE supervisor_routes ADD COLUMN shift_id VARCHAR(36) NULL"))
                print("  Added supervisor_routes.shift_id")

    # ── Transfer Methods: create table and seed defaults ──
    if not insp.has_table("transfer_methods"):
        print("  [migration] transfer_methods table will be created by create_all")
    else:
        # Seed default transfer methods if table is empty
        with engine.begin() as conn:
            count = conn.execute(sa_text("SELECT COUNT(*) FROM transfer_methods")).scalar()
            if count == 0:
                defaults = [
                    ("كويتي باي رول", "Kuwait Payroll", 0),
                    ("تحويل بنكي", "Bank Transfer", 1),
                    ("نقدي", "Cash", 2),
                    ("فودافون كاش", "Vodafone Cash", 3),
                ]
                import uuid as _uuid
                for (name_ar, name_en, order) in defaults:
                    conn.execute(sa_text(
                        "INSERT INTO transfer_methods (id, name, name_ar, is_active, sort_order) "
                        "VALUES (:id, :name, :name_ar, TRUE, :order)"
                    ), {"id": str(_uuid.uuid4()), "name": name_en, "name_ar": name_ar, "order": order})
                print("  [migration] Seeded default transfer methods")


    # ── Incident Categories: add category_id to existing incidents ──
    try:
        if insp.has_table("incidents"):
            existing = {c["name"] for c in insp.get_columns("incidents")}
            if "category_id" not in existing:
                with engine.begin() as conn:
                    conn.execute(sa_text("ALTER TABLE incidents ADD COLUMN category_id VARCHAR(36) NULL"))
                    print("  [migration] incidents.category_id added")
    except Exception as e:
        print(f"incidents migration: {e}")

    # ── Transfer Method Credits: seed one credit row per transfer method ──
    try:
        if insp.has_table("transfer_methods") and insp.has_table("transfer_method_credits"):
            with engine.begin() as conn:
                methods = conn.execute(sa_text("SELECT id, name FROM transfer_methods")).fetchall()
                for m in methods:
                    exists = conn.execute(
                        sa_text("SELECT COUNT(*) FROM transfer_method_credits WHERE transfer_method_id = :mid"),
                        {"mid": m[0]}
                    ).scalar()
                    if not exists:
                        conn.execute(sa_text(
                            "INSERT INTO transfer_method_credits (id, transfer_method_id, transfer_method_name, balance, total_topped_up, total_deducted, created_at, updated_at) "
                            "VALUES (:id, :mid, :name, 0.0, 0.0, 0.0, :now, :now)"
                        ), {"id": str(uuid.uuid4()), "mid": m[0], "name": m[1], "now": datetime.now(timezone.utc)})
                        print(f"  [migration] Created credit account for transfer method: {m[1]}")
    except Exception as e:
        print(f"transfer_method_credits migration: {e}")


def _seed_incident_categories(db):
    """Seed default incident categories if none exist."""
    import json as _json
    from app.models.incident_category import IncidentCategory as IncCat
    if db.query(IncCat).count() > 0:
        return
    import uuid as _uuid2
    defaults = [
        {
            "name": "Security Breach",
            "severity": "critical",
            "corrective_action": "Immediately secure the perimeter. Alert the supervisor and do not allow any unauthorized personnel to enter or leave. Document everything and await further instructions.",
            "alert_roles": _json.dumps(["admin", "supervisor"]),
        },
        {
            "name": "Unauthorized Access",
            "severity": "high",
            "corrective_action": "Escort the individual to the gate. Record their details and report to your supervisor immediately. Do not use force unless necessary.",
            "alert_roles": _json.dumps(["admin", "supervisor"]),
        },
        {
            "name": "Equipment Damage",
            "severity": "medium",
            "corrective_action": "Do not touch or move the damaged equipment. Photograph and document the damage. Report to your supervisor and await a maintenance inspection.",
            "alert_roles": _json.dumps(["admin"]),
        },
        {
            "name": "Property Damage",
            "severity": "high",
            "corrective_action": "Secure the area around the damage. Do not allow access until it has been assessed. Document with photos and report immediately.",
            "alert_roles": _json.dumps(["admin", "supervisor"]),
        },
        {
            "name": "Suspicious Activity",
            "severity": "medium",
            "corrective_action": "Observe and document the suspicious activity from a safe distance. Do not confront. Report to supervisor and remain vigilant.",
            "alert_roles": _json.dumps(["admin", "supervisor"]),
        },
        {
            "name": "Missing Guard",
            "severity": "high",
            "corrective_action": "Attempt to contact the guard immediately. If unreachable, report to supervisor and cover the post until a replacement arrives.",
            "alert_roles": _json.dumps(["admin", "supervisor"]),
        },
        {
            "name": "Other",
            "severity": "low",
            "corrective_action": "Document the incident thoroughly. Report to your supervisor and await further guidance.",
            "alert_roles": _json.dumps(["admin"]),
        },
    ]
    from datetime import datetime, timezone
    for d in defaults:
        cat = IncCat(
            category_id=str(_uuid2.uuid4()),
            name=d["name"],
            severity=d["severity"],
            corrective_action=d["corrective_action"],
            alert_roles_json=d["alert_roles"],
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.add(cat)
    db.commit()
    print(f"  [seed] Seeded {len(defaults)} default incident categories")


def seed():
    Base.metadata.create_all(bind=engine)
    _run_seed_migrations()

    db = SessionLocal()
    _seed_incident_categories(db)

    # ── Ensure Universal Tenant exists ──
    from app.models.task_models import Tenant
    uni_tenant = db.query(Tenant).filter_by(tenant_id="UNIVERSAL_TENANT").first()
    if not uni_tenant:
        uni_tenant = Tenant(
            tenant_id="UNIVERSAL_TENANT",
            tenant_code="UNIV01",
            name="SecureTrack Global",
            status="active",
        )
        db.add(uni_tenant)
        db.commit()

    if db.query(User).count() > 0:
        print("Users already exist. Making sure admin has UNIVERSAL_TENANT...")
        admin = db.query(User).filter_by(email="admin@securetrack.com").first()
        if admin and not admin.tenant_id:
            admin.tenant_id = "UNIVERSAL_TENANT"
            db.commit()
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
            "tenant_id": "UNIVERSAL_TENANT",
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
            "tenant_id": "UNIVERSAL_TENANT",
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
