"""
SecureTrack Platform — Data Snapshots API
Provides endpoints for listing, viewing, and rolling back import snapshots.
"""
import json
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text as sa_text

from app.core.database import get_db
from app.api.deps import get_current_user, require_role
from app.models.user import User
from app.models.data_snapshot import DataSnapshot
from app.enums import UserRole

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Helper: Create a snapshot before import ──
def create_import_snapshot(
    db: Session,
    table_name: str,
    before_rows: list,
    after_rows: list,
    user: User,
    description: str = "",
    operation: str = "import",
):
    """
    Call this from any import endpoint BEFORE committing the import.
    Stores the before/after state for rollback.
    """
    snapshot = DataSnapshot(
        table_name=table_name,
        operation=operation,
        description=description,
        performed_by=user.user_id,
        performed_by_name=user.name,
        rows_affected=len(after_rows),
        before_data=json.dumps(before_rows, default=str, ensure_ascii=False),
        after_data=json.dumps(after_rows, default=str, ensure_ascii=False),
    )
    db.add(snapshot)
    db.flush()
    return snapshot.snapshot_id


# ── List all snapshots ──
@router.get("", summary="List all data snapshots")
def list_snapshots(
    table_name: str = Query(None),
    status: str = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    q = db.query(DataSnapshot).order_by(DataSnapshot.created_at.desc())
    if table_name:
        q = q.filter(DataSnapshot.table_name == table_name)
    if status:
        q = q.filter(DataSnapshot.status == status)

    total = q.count()
    snapshots = q.offset(offset).limit(limit).all()

    return {
        "total": total,
        "snapshots": [
            {
                "snapshot_id": s.snapshot_id,
                "table_name": s.table_name,
                "operation": s.operation,
                "description": s.description,
                "performed_by_name": s.performed_by_name,
                "rows_affected": s.rows_affected,
                "status": s.status,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "rolled_back_at": s.rolled_back_at.isoformat() if s.rolled_back_at else None,
            }
            for s in snapshots
        ],
    }


# ── View snapshot detail (before/after diff) ──
@router.get("/{snapshot_id}", summary="View snapshot detail with before/after data")
def get_snapshot_detail(
    snapshot_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    snap = db.query(DataSnapshot).filter(DataSnapshot.snapshot_id == snapshot_id).first()
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    before = json.loads(snap.before_data) if snap.before_data else []
    after = json.loads(snap.after_data) if snap.after_data else []

    return {
        "snapshot_id": snap.snapshot_id,
        "table_name": snap.table_name,
        "operation": snap.operation,
        "description": snap.description,
        "performed_by_name": snap.performed_by_name,
        "rows_affected": snap.rows_affected,
        "status": snap.status,
        "created_at": snap.created_at.isoformat() if snap.created_at else None,
        "rolled_back_at": snap.rolled_back_at.isoformat() if snap.rolled_back_at else None,
        "before_data": before,
        "after_data": after,
    }


# ── Rollback a snapshot ──
@router.post("/{snapshot_id}/rollback", summary="Rollback an import to its before state")
def rollback_snapshot(
    snapshot_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    snap = db.query(DataSnapshot).filter(DataSnapshot.snapshot_id == snapshot_id).first()
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    if snap.status == "rolled_back":
        raise HTTPException(status_code=400, detail="Snapshot already rolled back")

    before_rows = json.loads(snap.before_data) if snap.before_data else []
    table = snap.table_name

    try:
        # Strategy: For each before_row, UPDATE the record back to its previous state.
        # If no primary key match, skip it (it was a new insert — we DELETE it).
        after_rows = json.loads(snap.after_data) if snap.after_data else []

        # Determine primary key column based on table
        pk_map = {
            "users": "user_id",
            "attendance_logs": "log_id",
            "daily_attendance_entries": "entry_id",
            "payroll_sheet_rows": "row_id",
            "bonus": "bonus_id",
            "cash_advances": "cash_advance_id",
            "guard_evaluations": "evaluation_id",
            "clothes_requests": "request_id",
            "vacation_requests": "id",
            "guard_documents": "document_id",
            "insurance_records": "record_id",
            "deduction_rules": "rule_id",
        }
        pk_col = pk_map.get(table, "id")

        # Build set of before-row PKs for quick lookup
        before_pk_set = {str(r.get(pk_col, "")) for r in before_rows if r.get(pk_col)}

        # Rows that were newly inserted (present in after but not in before)
        after_pk_set = {str(r.get(pk_col, "")) for r in after_rows if r.get(pk_col)}
        new_inserts = after_pk_set - before_pk_set

        # Delete newly inserted rows
        for pk_val in new_inserts:
            if pk_val:
                db.execute(sa_text(f"DELETE FROM `{table}` WHERE `{pk_col}` = :pk"), {"pk": pk_val})

        # Restore modified rows to before state
        for row in before_rows:
            pk_val = row.get(pk_col)
            if not pk_val:
                continue
            # Build UPDATE SET clause
            cols = [k for k in row.keys() if k != pk_col]
            if not cols:
                continue
            set_clause = ", ".join([f"`{c}` = :{c}" for c in cols])
            params = {c: row[c] for c in cols}
            params["pk"] = pk_val
            db.execute(
                sa_text(f"UPDATE `{table}` SET {set_clause} WHERE `{pk_col}` = :pk"),
                params,
            )

        snap.status = "rolled_back"
        snap.rolled_back_at = datetime.utcnow()
        db.commit()

        logger.info(f"Snapshot {snapshot_id} rolled back by {current_user.name}")
        return {"detail": f"Rollback successful. {len(before_rows)} rows restored, {len(new_inserts)} inserts removed."}

    except Exception as e:
        db.rollback()
        logger.error(f"Rollback failed for snapshot {snapshot_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Rollback failed: {str(e)}")


# ── Delete a snapshot ──
@router.delete("/{snapshot_id}", summary="Delete a snapshot record")
def delete_snapshot(
    snapshot_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    snap = db.query(DataSnapshot).filter(DataSnapshot.snapshot_id == snapshot_id).first()
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    db.delete(snap)
    db.commit()
    return {"detail": "Snapshot deleted"}
