from app.core.database import engine, Base
from app.models import *

print("Creating tables...")
Base.metadata.create_all(bind=engine)
print("Tables created.")
