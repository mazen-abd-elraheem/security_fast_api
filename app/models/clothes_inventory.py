from sqlalchemy import Column, Integer, String, DateTime, JSON, Float
from app.core.database import Base
from datetime import datetime

class ClothesRequest(Base):
    __tablename__ = "clothes_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_code = Column(String(50), nullable=True) # الكود
    request_date = Column(DateTime, default=datetime.utcnow) # التاريخ
    user_name = Column(String(150), nullable=True) # الاسم
    site_name = Column(String(150), nullable=True) # الفرع
    reason = Column(String(255), nullable=True) # السبب
    categories = Column(JSON, default=dict) # كاب وغيرها
    received_by = Column(String(150), nullable=True) # الموظف المستلم
    signature = Column(String(255), nullable=True) # التوقيع
    
    # Workflow fields
    status = Column(String(50), default="pending_ops") # pending_ops, pending_hr, approved, rejected
    ops_manager_id = Column(String(36), nullable=True)
    ops_reason = Column(String(255), nullable=True)
    hr_manager_id = Column(String(36), nullable=True)
    hr_reason = Column(String(255), nullable=True)
    
    # Extra field for association
    user_id = Column(String(36), nullable=True, index=True)


class ClothesTermination(Base):
    __tablename__ = "clothes_terminations"

    id = Column(Integer, primary_key=True, index=True)
    termination_date = Column(DateTime, default=datetime.utcnow) # تاريخ الترك
    user_name = Column(String(150), nullable=True) # الاسم
    site_name = Column(String(150), nullable=True) # الفرع
    supervisor_name = Column(String(150), nullable=True) # المشرف
    reason = Column(String(255), nullable=True) # السبب
    clothes_status = Column(String(100), nullable=True) # تسليم الزي
    notes = Column(String(255), nullable=True) 
    categories = Column(JSON, default=dict) 
    calculated_deduction = Column(Float, default=0.0)
    received_by = Column(String(150), nullable=True) # الموظف المستلم
    signature = Column(String(255), nullable=True) # التوقيع

    # Extra field for association
    user_id = Column(String(36), nullable=True, index=True)
