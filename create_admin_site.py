from app.core.database import engine, SessionLocal
from app.models.site import Site
from datetime import datetime, timezone

def create_admin_site():
    db = SessionLocal()
    try:
        # Check if it exists
        existing = db.query(Site).filter(Site.site_id == "admin_init").first()
        if not existing:
            print("Creating 'admin_init' dummy site...")
            dummy = Site(
                site_id="admin_init",
                name="Admin Initiated (No Site)",
                address="Headquarters",
                latitude=0.0,
                longitude=0.0,
                radius_meters=100,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(dummy)
            db.commit()
            print("Successfully created dummy site.")
        else:
            print("Dummy site 'admin_init' already exists.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    create_admin_site()
