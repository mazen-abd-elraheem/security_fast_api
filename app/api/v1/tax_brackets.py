"""
SecureTrack Platform - Tax Brackets API

DEPRECATION NOTICE:
  The TaxBracket model (accountant_models.TaxBracket) stored standalone
  bracket records that were NEVER consumed by the payroll engine.
  The actual tax calculation is driven by PayrollFormulaConfig entries
  under classification '__tax__' (bracket_1_limit, bracket_1_rate, ...).

  These CRUD endpoints are kept for backward compatibility only.
  To configure tax, use:
    PUT /accountant-sheet/formula-configs  with classification='__tax__'
  Keys:  bracket_1_limit, bracket_1_rate, bracket_2_limit, bracket_2_rate, ...
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import uuid

from app.core.database import get_db
from app.api.deps import require_role
from app.enums import UserRole
from app.models.user import User
from app.models.payroll_formula_config import PayrollFormulaConfig

router = APIRouter()


@router.get("", summary="[Deprecated] List tax config — use /accountant-sheet/formula-configs?classification=__tax__ instead")
def get_tax_brackets(
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    """
    Returns the active tax configuration from PayrollFormulaConfig (__tax__ classification).
    This is the single source of truth consumed by the payroll engine.
    """
    rows = db.query(PayrollFormulaConfig).filter(
        PayrollFormulaConfig.classification == "__tax__",
        PayrollFormulaConfig.is_active == True,
    ).order_by(PayrollFormulaConfig.config_key).all()

    return {
        "note": "Tax is configured via PayrollFormulaConfig (classification='__tax__'). "
                "Use PUT /accountant-sheet/formula-configs to update.",
        "brackets": [
            {
                "id": r.id,
                "config_key": r.config_key,
                "value": r.value,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    }


@router.post("", status_code=201, summary="[Deprecated] Upsert tax formula key via PayrollFormulaConfig")
def create_tax_bracket(
    config_key: str,
    value: float,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Upsert a tax config key in PayrollFormulaConfig under classification '__tax__'."""
    row = db.query(PayrollFormulaConfig).filter(
        PayrollFormulaConfig.classification == "__tax__",
        PayrollFormulaConfig.config_key == config_key,
    ).first()
    if row:
        row.value = value
        row.updated_by = current_user.user_id
    else:
        row = PayrollFormulaConfig(
            classification="__tax__",
            config_key=config_key,
            value=value,
            updated_by=current_user.user_id,
        )
        db.add(row)
    db.commit()
    return {"message": "Tax config key upserted", "config_key": config_key, "value": value}


@router.delete("/{config_key}", summary="[Deprecated] Delete a tax formula key")
def delete_tax_bracket(
    config_key: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    row = db.query(PayrollFormulaConfig).filter(
        PayrollFormulaConfig.classification == "__tax__",
        PayrollFormulaConfig.config_key == config_key,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Tax config key '{config_key}' not found")
    db.delete(row)
    db.commit()
    return {"message": f"Tax config key '{config_key}' deleted"}
