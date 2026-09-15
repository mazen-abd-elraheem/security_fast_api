"""
SecureTrack — Payroll Formula Config Model
Stores all formula constants per classification (replaces hardcoded Python dicts).
Admin can update values live without code changes.
"""
import uuid
from sqlalchemy import Column, String, Float, Boolean, DateTime, UniqueConstraint, Index
from datetime import datetime, timezone

from app.core.database import Base


class PayrollFormulaConfig(Base):
    __tablename__ = "payroll_formula_configs"
    __table_args__ = (
        UniqueConstraint("classification", "config_key", name="uq_formula_cls_key"),
        Index("ix_pfc_classification", "classification"),
    )

    id             = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    classification = Column(String(100), nullable=False)   # e.g. "فرد 8", "مشرف 12"
    config_key     = Column(String(50),  nullable=False)   # "daily_rate" | "incentive_rate" | ...
    value          = Column(Float, nullable=False, default=0.0)
    is_active      = Column(Boolean, nullable=False, default=True)
    updated_by     = Column(String(36), nullable=True)
    created_at     = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at     = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<PayrollFormulaConfig({self.classification}:{self.config_key}={self.value})>"


# Valid config keys (used for validation and seed)
FORMULA_CONFIG_KEYS = [
    "daily_rate",
    "incentive_rate",
    "increase_2025_rate",
    "bonus_rate",
    "annual_increase_base",
    "annual_increase_pct",  # fraction e.g. 0.30
]

# Tax bracket config is stored as special classification="__tax__"
# with config_key = "bracket_N_limit" and "bracket_N_rate" (N = 0..7)

# Default seed values (mirrors what was hardcoded in payroll_formulas.py)
DEFAULT_FORMULA_SEED = [
    # classification,          key,                   value
    ("\u0645\u0634\u0631\u0641 12",  "daily_rate",           2500 / 30),
    ("\u0645\u0634\u0631\u0641 12",  "incentive_rate",       1550),
    ("\u0645\u0634\u0631\u0641 12",  "increase_2025_rate",   900),
    ("\u0645\u0634\u0631\u0641 12",  "bonus_rate",           1750),
    ("\u0645\u0634\u0631\u0641 12",  "annual_increase_base", 500),
    ("\u0645\u0634\u0631\u0641 12",  "annual_increase_pct",  0.30),

    ("\u0645\u0634\u0631\u0641 8",   "daily_rate",           2000 / 30),
    ("\u0645\u0634\u0631\u0641 8",   "incentive_rate",       1150),
    ("\u0645\u0634\u0631\u0641 8",   "increase_2025_rate",   900),
    ("\u0645\u0634\u0631\u0641 8",   "bonus_rate",           1750),
    ("\u0645\u0634\u0631\u0641 8",   "annual_increase_base", 500),
    ("\u0645\u0634\u0631\u0641 8",   "annual_increase_pct",  0.30),

    ("\u0641\u0631\u062f 8",    "daily_rate",           1500 / 30),
    ("\u0641\u0631\u062f 8",    "incentive_rate",       1100),
    ("\u0641\u0631\u062f 8",    "increase_2025_rate",   600),
    ("\u0641\u0631\u062f 8",    "bonus_rate",           1750),
    ("\u0641\u0631\u062f 8",    "annual_increase_base", 500),
    ("\u0641\u0631\u062f 8",    "annual_increase_pct",  0.30),

    ("\u0641\u0631\u062f 12",   "daily_rate",           2000 / 30),
    ("\u0641\u0631\u062f 12",   "incentive_rate",       1100),
    ("\u0641\u0631\u062f 12",   "increase_2025_rate",   600),
    ("\u0641\u0631\u062f 12",   "bonus_rate",           1750),
    ("\u0641\u0631\u062f 12",   "annual_increase_base", 500),
    ("\u0641\u0631\u062f 12",   "annual_increase_pct",  0.30),

    ("\u0641\u0631\u062f 6",    "daily_rate",           1350 / 30),
    ("\u0641\u0631\u062f 6",    "incentive_rate",       1100),
    ("\u0641\u0631\u062f 6",    "increase_2025_rate",   450),
    ("\u0641\u0631\u062f 6",    "bonus_rate",           1300),
    ("\u0641\u0631\u062f 6",    "annual_increase_base", 500),
    ("\u0641\u0631\u062f 6",    "annual_increase_pct",  0.30),

    ("\u0641\u0631\u062f 4",    "daily_rate",           25.0),
    ("\u0641\u0631\u062f 4",    "incentive_rate",       1100),
    ("\u0641\u0631\u062f 4",    "increase_2025_rate",   300),
    ("\u0641\u0631\u062f 4",    "bonus_rate",           1200),
    ("\u0641\u0631\u062f 4",    "annual_increase_base", 500),
    ("\u0641\u0631\u062f 4",    "annual_increase_pct",  0.30),

    ("\u0644\u064a\u062f\u0649",     "daily_rate",           1500 / 30),
    ("\u0644\u064a\u062f\u0649",     "incentive_rate",       1100),
    ("\u0644\u064a\u062f\u0649",     "increase_2025_rate",   600),
    ("\u0644\u064a\u062f\u0649",     "bonus_rate",           1750),
    ("\u0644\u064a\u062f\u0649",     "annual_increase_base", 500),
    ("\u0644\u064a\u062f\u0649",     "annual_increase_pct",  0.30),

    ("\u0645\u062f\u064a\u0631",     "daily_rate",           6000 / 30),
    ("\u0645\u062f\u064a\u0631",     "incentive_rate",       1550),
    ("\u0645\u062f\u064a\u0631",     "increase_2025_rate",   1500),
    ("\u0645\u062f\u064a\u0631",     "bonus_rate",           1750),
    ("\u0645\u062f\u064a\u0631",     "annual_increase_base", 500),
    ("\u0645\u062f\u064a\u0631",     "annual_increase_pct",  0.30),

    ("\u0646\u0627\u0626\u0628",     "daily_rate",           2500 / 30),
    ("\u0646\u0627\u0626\u0628",     "incentive_rate",       1550),
    ("\u0646\u0627\u0626\u0628",     "increase_2025_rate",   1450),
    ("\u0646\u0627\u0626\u0628",     "bonus_rate",           1750),
    ("\u0646\u0627\u0626\u0628",     "annual_increase_base", 500),
    ("\u0646\u0627\u0626\u0628",     "annual_increase_pct",  0.30),

    ("\u062c\u0627\u0631\u062f",     "daily_rate",           1500 / 30),
    ("\u062c\u0627\u0631\u062f",     "incentive_rate",       1700),
    ("\u062c\u0627\u0631\u062f",     "increase_2025_rate",   600),
    ("\u062c\u0627\u0631\u062f",     "bonus_rate",           1750),
    ("\u062c\u0627\u0631\u062f",     "annual_increase_base", 800),
    ("\u062c\u0627\u0631\u062f",     "annual_increase_pct",  0.30),

    # Tax brackets  (classification="__tax__")
    ("__tax__",  "bracket_0_limit",   40000),
    ("__tax__",  "bracket_0_rate",    0.0),
    ("__tax__",  "bracket_1_limit",   55000),
    ("__tax__",  "bracket_1_rate",    0.10),
    ("__tax__",  "bracket_2_limit",   70000),
    ("__tax__",  "bracket_2_rate",    0.15),
    ("__tax__",  "bracket_3_limit",   200000),
    ("__tax__",  "bracket_3_rate",    0.20),
    ("__tax__",  "bracket_4_limit",   400000),
    ("__tax__",  "bracket_4_rate",    0.225),
    ("__tax__",  "bracket_5_limit",   600000),
    ("__tax__",  "bracket_5_rate",    0.25),
    ("__tax__",  "bracket_6_limit",   1200000),
    ("__tax__",  "bracket_6_rate",    0.25),
    ("__tax__",  "bracket_7_limit",   9999999),
    ("__tax__",  "bracket_7_rate",    0.275),
]
