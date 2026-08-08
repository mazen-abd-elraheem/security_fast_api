"""
SecureTrack Platform — Database Seed Script
Populates the database with realistic demo data for all roles.
"""
import uuid
import sys
import os
from datetime import datetime, timezone, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal, engine, Base
from app.models.user import User
from app.models.site import Site
from app.models.incident import Incident
from app.models.shift import Shift
from app.models.roster import RosterEntry
from app.core.security import hash_password


def seed():
    db = SessionLocal()

    try:
        # ── Check if data already exists ──
        existing_sites = db.query(Site).count()
        if existing_sites > 0:
            print(f"Database already has {existing_sites} sites. Skipping site seed.")
        else:
            print("Seeding sites...")

        # ── Get existing users ──
        admin_user = db.query(User).filter(User.role == "super_admin").first()
        supervisor = db.query(User).filter(User.role == "supervisor").first()
        guard = db.query(User).filter(User.role == "guard").first()

        if not admin_user:
            print("ERROR: No super_admin user found. Please register one first.")
            return

        admin_id = admin_user.user_id
        supervisor_id = supervisor.user_id if supervisor else admin_id
        guard_id = guard.user_id if guard else admin_id

        # ── Create additional guard users ──
        guard_users = []
        guard_data = [
            ("John Doe", "john.doe@securetrack.com", "guard", "G-101"),
            ("Alice Smith", "alice.smith@securetrack.com", "guard", "G-102"),
            ("Robert Jones", "robert.jones@securetrack.com", "guard", "G-103"),
            ("Maria Garcia", "maria.garcia@securetrack.com", "guard", "G-104"),
            ("Tom Wilson", "tom.wilson@securetrack.com", "guard", "G-105"),
            ("Karen Lee", "karen.lee@securetrack.com", "guard", "G-106"),
            ("Mike Chang", "mike.chang@securetrack.com", "guard", "G-107"),
            ("Sarah Davis", "sarah.davis@securetrack.com", "guard", "G-108"),
        ]
        for name, email, role, badge in guard_data:
            existing = db.query(User).filter(User.email == email).first()
            if not existing:
                u = User(
                    user_id=str(uuid.uuid4()),
                    name=name,
                    email=email,
                    phone_number=f"+1-555-{str(uuid.uuid4().int)[:4]}",
                    password_hash=hash_password("Guard123!"),
                    role=role,
                    badge_number=badge,
                    is_active=True,
                )
                db.add(u)
                guard_users.append(u)
                print(f"  Created guard: {name} ({email})")
            else:
                guard_users.append(existing)

        # ── Create additional supervisors ──
        sup_data = [
            ("Sgt. Miller", "sgt.miller@securetrack.com", "supervisor", "SUP-04"),
            ("Sgt. Barnes", "sgt.barnes@securetrack.com", "supervisor", "SUP-09"),
        ]
        for name, email, role, badge in sup_data:
            existing = db.query(User).filter(User.email == email).first()
            if not existing:
                u = User(
                    user_id=str(uuid.uuid4()),
                    name=name,
                    email=email,
                    phone_number=f"+1-555-{str(uuid.uuid4().int)[:4]}",
                    password_hash=hash_password("Super123!"),
                    role=role,
                    badge_number=badge,
                    is_active=True,
                )
                db.add(u)
                print(f"  Created supervisor: {name} ({email})")

        db.flush()

        # ── Sites ──
        sites_data = [
            {
                "name": "Alpha Sector 4",
                "address": "1200 Industrial Pkwy, DTLA",
                "latitude": 34.0522,
                "longitude": -118.2437,
                "radius_meters": 500,
                "region": "North",
                "status": "active",
            },
            {
                "name": "Delta Warehouse",
                "address": "4500 Harbor Blvd, Long Beach",
                "latitude": 33.7701,
                "longitude": -118.1937,
                "radius_meters": 1200,
                "region": "South",
                "status": "active",
            },
            {
                "name": "Echo Substation",
                "address": "789 Grid Lane, Pasadena",
                "latitude": 34.1478,
                "longitude": -118.1445,
                "radius_meters": 250,
                "region": "East",
                "status": "maintenance",
            },
            {
                "name": "Bravo Command Center",
                "address": "350 Command Ave, Burbank",
                "latitude": 34.1808,
                "longitude": -118.3090,
                "radius_meters": 800,
                "region": "North",
                "status": "active",
            },
            {
                "name": "Charlie Data Center",
                "address": "1100 Server Rd, El Segundo",
                "latitude": 33.9192,
                "longitude": -118.4165,
                "radius_meters": 300,
                "region": "West",
                "status": "active",
            },
            {
                "name": "Foxtrot Logistics Hub",
                "address": "6789 Freight Way, Carson",
                "latitude": 33.8317,
                "longitude": -118.2620,
                "radius_meters": 1000,
                "region": "South",
                "status": "active",
            },
            {
                "name": "Golf Research Lab",
                "address": "222 Innovation Dr, Irvine",
                "latitude": 33.6846,
                "longitude": -117.8265,
                "radius_meters": 400,
                "region": "East",
                "status": "active",
            },
            {
                "name": "Hotel Convention Center",
                "address": "1000 Convention Way, Anaheim",
                "latitude": 33.8003,
                "longitude": -117.9190,
                "radius_meters": 600,
                "region": "East",
                "status": "active",
            },
            {
                "name": "India Power Plant",
                "address": "4400 Energy Blvd, Redondo Beach",
                "latitude": 33.8492,
                "longitude": -118.3884,
                "radius_meters": 750,
                "region": "West",
                "status": "active",
            },
            {
                "name": "Juliet Medical Campus",
                "address": "900 Health Center Dr, Torrance",
                "latitude": 33.8358,
                "longitude": -118.3406,
                "radius_meters": 350,
                "region": "West",
                "status": "active",
            },
            {
                "name": "Kilo Shipping Terminal",
                "address": "Port of LA, Terminal Island",
                "latitude": 33.7405,
                "longitude": -118.2726,
                "radius_meters": 1500,
                "region": "South",
                "status": "active",
            },
            {
                "name": "Lima Financial District",
                "address": "555 Wall St, Downtown LA",
                "latitude": 34.0505,
                "longitude": -118.2551,
                "radius_meters": 200,
                "region": "North",
                "status": "active",
            },
        ]

        created_sites = []
        for s in sites_data:
            existing = db.query(Site).filter(Site.name == s["name"]).first()
            if not existing:
                site = Site(
                    site_id=str(uuid.uuid4()),
                    name=s["name"],
                    address=s["address"],
                    latitude=s["latitude"],
                    longitude=s["longitude"],
                    radius_meters=s["radius_meters"],
                    region=s["region"],
                    status=s["status"],
                )
                db.add(site)
                created_sites.append(site)
                print(f"  Created site: {s['name']}")
            else:
                created_sites.append(existing)

        db.flush()

        # ── Incidents ──
        now = datetime.now(timezone.utc)
        incidents_data = [
            {
                "title": "Unauthorized Access Attempt - Sector 7G",
                "description": "Unidentified individual attempted to enter through the north perimeter gate without proper credentials. Security footage captured.",
                "category": "unauthorized_access",
                "severity": "critical",
                "status": "open",
                "site_idx": 0,
            },
            {
                "title": "Equipment Malfunction - Camera Feed Lost",
                "description": "CCTV cameras in Loading Dock B went offline at 13:15. Technician dispatched for inspection.",
                "category": "equipment_damage",
                "severity": "high",
                "status": "investigating",
                "site_idx": 1,
            },
            {
                "title": "Perimeter Breach Detected at North Fence",
                "description": "Motion sensors triggered along the northern fence line. Guard patrol dispatched to investigate.",
                "category": "security_breach",
                "severity": "critical",
                "status": "open",
                "site_idx": 0,
            },
            {
                "title": "Suspicious Vehicle in Parking Lot C",
                "description": "Unregistered white van observed circling the parking lot at 02:30 AM. License plate recorded.",
                "category": "suspicious_activity",
                "severity": "high",
                "status": "investigating",
                "site_idx": 3,
            },
            {
                "title": "Fire Alarm Triggered - Building 3",
                "description": "Fire alarm activated on Floor 2. Initial assessment: false alarm due to construction dust.",
                "category": "other",
                "severity": "medium",
                "status": "resolved",
                "site_idx": 4,
            },
            {
                "title": "Guard No-Show - Night Shift Post 5",
                "description": "Assigned guard failed to report for 22:00-06:00 shift. Replacement guard deployed from reserve.",
                "category": "missing_guard",
                "severity": "high",
                "status": "resolved",
                "site_idx": 5,
            },
            {
                "title": "Tailgating Incident at Main Entrance",
                "description": "Employee held door open for unescorted visitor. Both individuals identified and logged.",
                "category": "security_breach",
                "severity": "medium",
                "status": "open",
                "site_idx": 6,
            },
            {
                "title": "Vandalism - Exterior Wall Graffiti",
                "description": "Graffiti discovered on the east wall during morning patrol. Photos documented for facility management.",
                "category": "property_damage",
                "severity": "low",
                "status": "open",
                "site_idx": 7,
            },
            {
                "title": "Server Room Temperature Alert",
                "description": "Temperature sensor in Server Room B reading 32°C (threshold: 28°C). HVAC maintenance notified.",
                "category": "equipment_damage",
                "severity": "high",
                "status": "investigating",
                "site_idx": 4,
            },
            {
                "title": "Delivery Driver Altercation",
                "description": "Verbal confrontation between delivery driver and gate guard regarding documentation. De-escalated by supervisor.",
                "category": "other",
                "severity": "medium",
                "status": "resolved",
                "site_idx": 1,
            },
        ]

        for i, inc in enumerate(incidents_data):
            existing = db.query(Incident).filter(Incident.title == inc["title"]).first()
            if not existing:
                incident = Incident(
                    incident_id=str(uuid.uuid4()),
                    site_id=created_sites[inc["site_idx"]].site_id,
                    reported_by=guard_id if guard else admin_id,
                    title=inc["title"],
                    description=inc["description"],
                    category=inc["category"],
                    severity=inc["severity"],
                    status=inc["status"],
                    created_at=now - timedelta(hours=i * 2, minutes=i * 7),
                )
                db.add(incident)
                print(f"  Created incident: {inc['title'][:50]}...")

        db.commit()
        print("\n✅ Database seeded successfully!")
        print(f"   Sites: {db.query(Site).count()}")
        print(f"   Users: {db.query(User).count()}")
        print(f"   Incidents: {db.query(Incident).count()}")

    except Exception as e:
        db.rollback()
        print(f"❌ Seed failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    seed()
