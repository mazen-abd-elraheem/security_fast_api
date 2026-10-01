"""
SecureTrack Platform — Data Snapshot Model
Stores before/after snapshots of table data on import operations.
Used for auditing and rollback of bad imports.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Text, Integer
from app.core.database import Base


class DataSnapshot(Base):
    __tablename__ = "data_snapshots"

    snapshot_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    table_name = Column(String(100), nullable=False, index=True)
    operation = Column(String(50), nullable=False, default="import")  # import, bulk_update, delete
    description = Column(String(500), nullable=True)
    performed_by = Column(String(36), nullable=False)  # user_id
    performed_by_name = Column(String(200), nullable=True)
    rows_affected = Column(Integer, default=0)
    before_data = Column(Text, nullable=True)  # JSON: list of affected rows BEFORE
    after_data = Column(Text, nullable=True)   # JSON: list of affected rows AFTER
    status = Column(String(20), default="active")  # active, rolled_back
    rolled_back_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
