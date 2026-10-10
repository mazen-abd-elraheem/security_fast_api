"""
SecureTrack — Custody Disbursement API

Cash types (salary / cash_advance):
    accountant requests  → pending_admin
    admin approves (+ optional transfer-method credit deduction) → pending_handoff
Item types (clothes / device):
    personnel officer requests → pending_hr (or pending_issue if the clothes
    request is already HR-approved)
    HR approves → pending_issue
    personnel officer issues to a supervisor → pending_receipt
    supervisor confirms receipt → pending_handoff
Then, for every type:
    supervisor hands to guard → handed
    guard confirms (amount + photo) → confirmed | disputed
"""
import os
import shutil
import uuid
from datetime import datetime, timezone, date, timedelta
from typing import Optional, List, Set

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.api.deps import require_role, get_current_user
from app.enums import UserRole
from app.models.custody_disbursement import CustodyDisbursement
from app.models.user import User
from app.models.site import Site
from app.models.shift import Shift
from app.models.guard_roster import GuardRoster
from app.models.supervisor_route import SupervisorRoute
from app.models.notification import Notification

router = APIRouter()

PHOTO_SUBDIR = "custody_photos"
PHOTO_DIR = os.path.join(settings.UPLOAD_DIR, PHOTO_SUBDIR)
os.makedirs(PHOTO_DIR, exist_ok=True)

CASH_TYPES = {"salary", "cash_advance"}
ITEM_TYPES = {"clothes", "device"}
ALL_TYPES = CASH_TYPES | ITEM_TYPES
OPEN_STATUSES = ["pending_admin", "pending_hr", "pending_issue", "pending_receipt",
                 "pending_handoff", "handed"]
GUARD_ROLES = ["guard", "leader", "outdoor", "lady"]
ROSTER_WINDOW_DAYS = 35


# ── Schemas ───────────────────────────────────────────────────────────────────

class CustodyCreateBody(BaseModel):
    guard_id: str
    custody_type: str                 # salary | cash_advance | clothes | device
    amount: Optional[float] = None
    description: Optional[str] = None
    ref_id: Optional[str] = None      # advance_id / clothes request id / "YYYY-MM" for salary
    site_id: Optional[str] = None


class ApproveBody(BaseModel):
    notes: Optional[str] = None
    # Cash types only (admin step)
    transfer_method_id: Optional[str] = None
    deduct_credit: bool = True
    amount: Optional[float] = None    # admin may modify the amount


class RejectBody(BaseModel):
    notes: str


class IssueBody(BaseModel):
    supervisor_id: str
    notes: Optional[str] = None


class MarkHandedBody(BaseModel):
    supervisor_notes: Optional[str] = None


class ResolveBody(BaseModel):
    action: str                       # reopen | cancel
    notes: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _role(u: User) -> str:
    r = u.role
    return str(getattr(r, "value", r))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def _to_dict(d: CustodyDisbursement) -> dict:
    mismatch = (
        d.amount is not None and d.guard_confirmed_amount is not None
        and abs(float(d.amount) - float(d.guard_confirmed_amount)) > 0.01
    )
    return {
        "disbursement_id": d.disbursement_id,
        "custody_type": d.custody_type,
        "ref_id": d.ref_id,
        "supervisor_id": d.supervisor_id,
        "supervisor_name": d.supervisor_name,
        "guard_id": d.guard_id,
        "guard_name": d.guard_name,
        "guard_badge": d.guard_badge,
        "site_id": d.site_id,
        "site_name": d.site_name,
        "amount": d.amount,
        "description": d.description,
        "status": d.status,
        "requested_by_name": d.requested_by_name,
        "approved_by_name": d.approved_by_name,
        "approved_at": _iso(d.approved_at),
        "approval_notes": d.approval_notes or "",
        "transfer_method_id": d.transfer_method_id,
        "transfer_method_name": d.transfer_method_name,
        "credit_deducted": d.credit_deducted or 0.0,
        "issued_at": _iso(d.issued_at),
        "supervisor_received_at": _iso(d.supervisor_received_at),
        "handed_at": _iso(d.handed_at),
        "supervisor_notes": d.supervisor_notes or "",
        "guard_confirmed_amount": d.guard_confirmed_amount,
        "amount_mismatch": mismatch,
        "guard_photo_url": d.guard_photo_url,
        "guard_notes": d.guard_notes or "",
        "confirmed_at": _iso(d.confirmed_at),
        "created_at": _iso(d.created_at),
    }


def _users_with_role(db: Session, *roles: UserRole) -> List[str]:
    vals = [r.value for r in roles]
    rows = db.query(User.user_id).filter(User.role.in_(vals), User.is_active == True).all()
    return [r[0] for r in rows]


def _notify(db: Session, user_ids, title: str, message: str, ref_id: Optional[str] = None):
    for uid in {u for u in user_ids if u}:
        db.add(Notification(
            notification_id=str(uuid.uuid4()),
            user_id=uid,
            notif_type="custody",
            title=title,
            message=message,
            reference_id=ref_id,
            reference_type="custody",
        ))


def _since() -> date:
    return date.today() - timedelta(days=ROSTER_WINDOW_DAYS)


def _supervisor_site_ids(db: Session, user: User) -> Set[str]:
    """Sites a supervisor/leader works at: assigned routes + guard-roster links."""
    ids = {r[0] for r in db.query(SupervisorRoute.site_id)
           .filter(SupervisorRoute.supervisor_id == user.user_id).distinct().all()}
    link = GuardRoster.supervisor_id == user.user_id
    if _role(user) == "leader":
        link = or_(GuardRoster.supervisor_id == user.user_id, GuardRoster.leader_id == user.user_id)
    rows = (db.query(Shift.site_id)
            .join(GuardRoster, GuardRoster.shift_id == Shift.shift_id)
            .filter(link).distinct().all())
    ids |= {r[0] for r in rows}
    return ids


def _guards_on_roster(db: Session, site_id: str, shift_id: Optional[str]) -> List[str]:
    q = (db.query(GuardRoster.guard_id)
         .join(Shift, Shift.shift_id == GuardRoster.shift_id)
         .filter(Shift.site_id == site_id,
                 GuardRoster.assigned_date >= _since(),
                 GuardRoster.status != "canceled"))
    if shift_id:
        q = q.filter(GuardRoster.shift_id == shift_id)
    return [r[0] for r in q.distinct().all()]


def _guard_site(db: Session, guard_id: str):
    """Most recent site a guard was rostered at → (site_id, site_name)."""
    row = (db.query(Site.site_id, Site.name)
           .join(Shift, Shift.site_id == Site.site_id)
           .join(GuardRoster, GuardRoster.shift_id == Shift.shift_id)
           .filter(GuardRoster.guard_id == guard_id)
           .order_by(GuardRoster.assigned_date.desc())
           .first())
    return (row[0], row[1]) if row else (None, None)


def _get_record(db: Session, disbursement_id: str) -> CustodyDisbursement:
    rec = db.query(CustodyDisbursement).filter(
        CustodyDisbursement.disbursement_id == disbursement_id,
        CustodyDisbursement.is_active == True,
    ).first()
    if not rec:
        raise HTTPException(404, "Disbursement not found")
    return rec


def _already_requested(db: Session, guard_id: str, custody_type: str, ref_id: Optional[str]) -> bool:
    if not ref_id:
        return False
    return db.query(CustodyDisbursement).filter(
        CustodyDisbursement.guard_id == guard_id,
        CustodyDisbursement.custody_type == custody_type,
        CustodyDisbursement.ref_id == ref_id,
        CustodyDisbursement.is_active == True,
        CustodyDisbursement.status.notin_(["rejected", "cancelled"]),
    ).first() is not None


def _check_supervisor_access(db: Session, user: User, rec: CustodyDisbursement):
    """Supervisor may act on a record if it's assigned to them or sits at one of their sites."""
    if rec.supervisor_id and rec.supervisor_id != user.user_id:
        raise HTTPException(403, "This custody belongs to another supervisor")
    if not rec.supervisor_id and rec.site_id:
        if rec.site_id not in _supervisor_site_ids(db, user) and \
                rec.guard_id not in _guards_on_roster(db, rec.site_id, None):
            raise HTTPException(403, "Not authorized for this site")


# ── POST / — create a custody request ────────────────────────────────────────

@router.post("/", status_code=201, summary="Create a custody request")
def create_disbursement(
    data: CustodyCreateBody,
    current_user: User = Depends(require_role(
        UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.HR, UserRole.PERSONNEL_OFFICER,
    )),
    db: Session = Depends(get_db),
):
    """
    Cash types  (salary / cash_advance): accountant/admin  → pending_admin
    Item types  (clothes / device):      personnel/HR/admin → pending_hr
                (clothes whose request is already HR-approved → pending_issue)
    """
    ctype = data.custody_type
    if ctype not in ALL_TYPES:
        raise HTTPException(400, f"custody_type must be one of {sorted(ALL_TYPES)}")

    role = _role(current_user)
    if ctype in CASH_TYPES and role not in ("accountant", "admin"):
        raise HTTPException(403, "Only accountant/admin can request cash custody")
    if ctype in ITEM_TYPES and role not in ("personnel_officer", "hr", "admin"):
        raise HTTPException(403, "Only personnel officer/HR/admin can request item custody")

    guard = db.query(User).filter(User.user_id == data.guard_id).first()
    if not guard:
        raise HTTPException(404, "Guard not found")

    if ctype in CASH_TYPES and (data.amount is None or data.amount <= 0):
        raise HTTPException(400, "A positive amount is required for cash custody")

    if _already_requested(db, guard.user_id, ctype, data.ref_id):
        raise HTTPException(409, "A custody request already exists for this reference")

    site_id, site_name = data.site_id, None
    if site_id:
        site = db.query(Site).filter(Site.site_id == site_id).first()
        site_name = site.name if site else None
    else:
        site_id, site_name = _guard_site(db, guard.user_id)

    if ctype in CASH_TYPES:
        status = "pending_admin"
    else:
        status = "pending_hr"
        if ctype == "clothes" and data.ref_id and data.ref_id.isdigit():
            from app.models.clothes_inventory import ClothesRequest
            cr = db.query(ClothesRequest).filter(ClothesRequest.id == int(data.ref_id)).first()
            if cr and cr.status == "approved":
                status = "pending_issue"

    d = CustodyDisbursement(
        disbursement_id=str(uuid.uuid4()),
        custody_type=ctype,
        ref_id=data.ref_id,
        guard_id=guard.user_id,
        guard_name=guard.name,
        guard_badge=guard.badge_number,
        site_id=site_id,
        site_name=site_name,
        amount=data.amount,
        description=data.description,
        status=status,
        requested_by=current_user.user_id,
        requested_by_name=current_user.name,
        is_active=True,
    )
    db.add(d)

    label = f"{guard.name} ({ctype})"
    if status == "pending_admin":
        _notify(db, _users_with_role(db, UserRole.ADMIN), "Custody approval needed",
                f"{current_user.name} requested {data.amount:.2f} EGP for {label}",
                d.disbursement_id)
    elif status == "pending_hr":
        _notify(db, _users_with_role(db, UserRole.HR), "Custody approval needed",
                f"{current_user.name} requested {label}", d.disbursement_id)
    else:
        _notify(db, _users_with_role(db, UserRole.PERSONNEL_OFFICER), "Items ready to issue",
                f"Issue {label} to a supervisor", d.disbursement_id)

    db.commit()
    db.refresh(d)
    return _to_dict(d)


# ── Approval step (admin for cash, HR for items) ─────────────────────────────

@router.post("/{disbursement_id}/approve", summary="Admin/HR approves a custody request")
def approve(
    disbursement_id: str,
    data: ApproveBody,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.CEO, UserRole.HR)),
    db: Session = Depends(get_db),
):
    rec = _get_record(db, disbursement_id)
    role = _role(current_user)

    if rec.status == "pending_admin":
        if role not in ("admin", "ceo"):
            raise HTTPException(403, "Only admin can approve cash custody")

        if data.amount is not None:
            if data.amount <= 0:
                raise HTTPException(400, "Amount must be positive")
            rec.amount = data.amount

        if data.deduct_credit and (rec.amount or 0) > 0:
            if not data.transfer_method_id:
                raise HTTPException(400, "transfer_method_id is required to deduct credit")
            from app.models.accountant_models import TransferMethod
            from app.models.transfer_method_credit import TransferMethodCredit, TransferMethodCreditLog
            method = db.query(TransferMethod).filter(TransferMethod.id == data.transfer_method_id).first()
            if not method:
                raise HTTPException(404, "Transfer method not found")
            credit = db.query(TransferMethodCredit).filter(
                TransferMethodCredit.transfer_method_id == method.id
            ).first()
            available = credit.balance if credit else 0.0
            if credit is None or available < rec.amount:
                raise HTTPException(402, detail={
                    "message": "Insufficient transfer method credit",
                    "method": method.name,
                    "needed": round(rec.amount, 2),
                    "available": round(available, 2),
                })
            now = _now()
            credit.balance = round(credit.balance - rec.amount, 2)
            credit.total_deducted = round(credit.total_deducted + rec.amount, 2)
            credit.updated_at = now
            db.add(TransferMethodCreditLog(
                id=str(uuid.uuid4()),
                credit_id=credit.id,
                operation="deduct",
                amount=rec.amount,
                balance_after=credit.balance,
                reference=f"Custody {rec.custody_type} — {rec.guard_name}",
                actor_id=current_user.user_id,
                actor_name=current_user.name,
                created_at=now,
            ))
            rec.transfer_method_id = method.id
            rec.transfer_method_name = method.name
            rec.credit_deducted = rec.amount

        rec.status = "pending_handoff"
        _notify(db, [rec.requested_by], "Custody approved",
                f"{rec.guard_name}: {rec.amount:.2f} EGP approved by {current_user.name}",
                rec.disbursement_id)

    elif rec.status == "pending_hr":
        if role not in ("hr", "admin"):
            raise HTTPException(403, "Only HR can approve item custody")
        rec.status = "pending_issue"
        _notify(db, _users_with_role(db, UserRole.PERSONNEL_OFFICER) + [rec.requested_by],
                "Items ready to issue",
                f"Issue {rec.guard_name}'s {rec.custody_type} to a supervisor",
                rec.disbursement_id)
    else:
        raise HTTPException(400, f"Cannot approve in status: {rec.status}")

    rec.approved_by = current_user.user_id
    rec.approved_by_name = current_user.name
    rec.approved_at = _now()
    rec.approval_notes = data.notes
    rec.updated_at = _now()
    db.commit()
    db.refresh(rec)
    return _to_dict(rec)


@router.post("/{disbursement_id}/reject", summary="Admin/HR rejects a custody request")
def reject(
    disbursement_id: str,
    data: RejectBody,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.CEO, UserRole.HR)),
    db: Session = Depends(get_db),
):
    rec = _get_record(db, disbursement_id)
    role = _role(current_user)
    if rec.status == "pending_admin" and role not in ("admin", "ceo"):
        raise HTTPException(403, "Only admin can reject cash custody")
    if rec.status == "pending_hr" and role not in ("hr", "admin"):
        raise HTTPException(403, "Only HR can reject item custody")
    if rec.status not in ("pending_admin", "pending_hr"):
        raise HTTPException(400, f"Cannot reject in status: {rec.status}")

    rec.status = "rejected"
    rec.approved_by = current_user.user_id
    rec.approved_by_name = current_user.name
    rec.approved_at = _now()
    rec.approval_notes = data.notes
    rec.updated_at = _now()
    _notify(db, [rec.requested_by], "Custody request rejected",
            f"{rec.guard_name} ({rec.custody_type}): {data.notes}", rec.disbursement_id)
    db.commit()
    db.refresh(rec)
    return _to_dict(rec)


# ── Item flow: personnel issues → supervisor receives ────────────────────────

@router.post("/{disbursement_id}/issue", summary="Personnel officer issues items to a supervisor")
def issue_to_supervisor(
    disbursement_id: str,
    data: IssueBody,
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    rec = _get_record(db, disbursement_id)
    if rec.status != "pending_issue":
        raise HTTPException(400, f"Cannot issue in status: {rec.status}")

    sup = db.query(User).filter(
        User.user_id == data.supervisor_id,
        User.role.in_([UserRole.SUPERVISOR.value, UserRole.LEADER.value]),
    ).first()
    if not sup:
        raise HTTPException(404, "Supervisor not found")

    rec.supervisor_id = sup.user_id
    rec.supervisor_name = sup.name
    rec.status = "pending_receipt"
    rec.issued_at = _now()
    rec.updated_at = _now()
    if data.notes:
        rec.approval_notes = ((rec.approval_notes or "") + f"\n[issue] {data.notes}").strip()
    _notify(db, [sup.user_id], "Items issued to you",
            f"Confirm receipt of {rec.custody_type} for {rec.guard_name}", rec.disbursement_id)
    db.commit()
    db.refresh(rec)
    return _to_dict(rec)


def _sync_sent_clothes(db: Session):
    try:
        from app.models.clothes_inventory import ClothesRequest
        from app.api.v1.clothes_inventory import sync_clothes_request_to_custody
        sent_reqs = db.query(ClothesRequest).filter(ClothesRequest.status == "sent").all()
        for sr in sent_reqs:
            sync_clothes_request_to_custody(sr, db)
        db.commit()
    except Exception:
        db.rollback()


@router.get("/incoming", summary="Supervisor: items issued to me awaiting receipt")
def incoming_for_supervisor(
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    _sync_sent_clothes(db)
    sup_sites = _supervisor_site_ids(db, current_user)
    recs = db.query(CustodyDisbursement).filter(
        CustodyDisbursement.status == "pending_receipt",
        CustodyDisbursement.is_active == True,
        or_(
            CustodyDisbursement.supervisor_id == current_user.user_id,
            and_(
                CustodyDisbursement.supervisor_id == None,
                CustodyDisbursement.site_id.in_(sup_sites) if sup_sites else False
            )
        )
    ).order_by(CustodyDisbursement.issued_at.desc()).all()
    return [_to_dict(r) for r in recs]


@router.post("/{disbursement_id}/receive", summary="Supervisor confirms receipt of issued items")
def supervisor_receive(
    disbursement_id: str,
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    rec = _get_record(db, disbursement_id)
    if rec.status != "pending_receipt":
        raise HTTPException(400, f"Cannot receive in status: {rec.status}")
    if rec.supervisor_id and rec.supervisor_id != current_user.user_id:
        raise HTTPException(403, "These items were issued to another supervisor")

    rec.supervisor_id = current_user.user_id
    rec.supervisor_name = current_user.name
    rec.status = "pending_handoff"
    rec.supervisor_received_at = _now()
    rec.updated_at = _now()
    if rec.ref_id and rec.ref_id.isdigit():
        from app.models.clothes_inventory import ClothesRequest
        cr = db.query(ClothesRequest).filter(ClothesRequest.id == int(rec.ref_id)).first()
        if cr:
            cr.received_by = current_user.name
    _notify(db, [rec.requested_by], "Supervisor received items",
            f"{current_user.name} received {rec.custody_type} for {rec.guard_name}",
            rec.disbursement_id)
    db.commit()
    db.refresh(rec)
    return _to_dict(rec)



# ── Supervisor: pending list, hand to guard, history ─────────────────────────

@router.get("/my-sites", summary="Supervisor: sites I work at")
def my_sites(
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    ids = _supervisor_site_ids(db, current_user)
    if not ids:
        return []
    sites = db.query(Site).filter(Site.site_id.in_(ids)).order_by(Site.name).all()
    return [{"site_id": s.site_id, "name": s.name} for s in sites]


@router.get("/site-shifts", summary="Shifts of a site")
def site_shifts(
    site_id: str = Query(...),
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    shifts = db.query(Shift).filter(Shift.site_id == site_id, Shift.is_active == True)\
        .order_by(Shift.start_time).all()
    return [{
        "shift_id": s.shift_id,
        "label": s.label or "",
        "start_time": s.start_time.strftime("%H:%M") if s.start_time else "",
        "end_time": s.end_time.strftime("%H:%M") if s.end_time else "",
    } for s in shifts]


@router.get("/pending", summary="Supervisor: pending hand-offs by type / site / shift")
def get_pending_for_supervisor(
    site_id: str = Query(...),
    custody_type: str = Query(..., description="salary | cash_advance | clothes | device"),
    shift_id: Optional[str] = Query(None),
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    if site_id not in _supervisor_site_ids(db, current_user):
        raise HTTPException(403, "Not authorized for this site")

    q = db.query(CustodyDisbursement).filter(
        CustodyDisbursement.custody_type == custody_type,
        CustodyDisbursement.is_active == True,
        CustodyDisbursement.status.in_(["pending_handoff", "handed"]),
        or_(CustodyDisbursement.supervisor_id == None,
            CustodyDisbursement.supervisor_id == current_user.user_id),
    )
    if shift_id:
        q = q.filter(CustodyDisbursement.guard_id.in_(_guards_on_roster(db, site_id, shift_id) or ["-"]))
    else:
        guards = _guards_on_roster(db, site_id, None)
        cond = CustodyDisbursement.site_id == site_id
        if guards:
            cond = or_(cond, CustodyDisbursement.guard_id.in_(guards))
        q = q.filter(cond)

    return [_to_dict(r) for r in q.order_by(CustodyDisbursement.created_at.desc()).all()]


@router.get("/history", summary="Supervisor: my distribution history")
def supervisor_history(
    custody_type: Optional[str] = Query(None),
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    q = db.query(CustodyDisbursement).filter(
        CustodyDisbursement.supervisor_id == current_user.user_id,
        CustodyDisbursement.is_active == True,
        CustodyDisbursement.status.in_(["handed", "confirmed", "disputed"]),
    )
    if custody_type:
        q = q.filter(CustodyDisbursement.custody_type == custody_type)
    return [_to_dict(r) for r in q.order_by(CustodyDisbursement.created_at.desc()).limit(200).all()]


@router.post("/{disbursement_id}/hand", summary="Supervisor marks custody as handed to guard")
def mark_handed(
    disbursement_id: str,
    data: MarkHandedBody,
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    rec = _get_record(db, disbursement_id)
    if rec.status != "pending_handoff":
        raise HTTPException(400, f"Cannot hand in status: {rec.status}")
    _check_supervisor_access(db, current_user, rec)

    rec.status = "handed"
    rec.supervisor_id = current_user.user_id
    rec.supervisor_name = current_user.name
    rec.supervisor_notes = data.supervisor_notes
    rec.handed_at = _now()
    rec.updated_at = _now()
    if rec.ref_id and rec.ref_id.isdigit():
        from app.models.clothes_inventory import ClothesRequest
        cr = db.query(ClothesRequest).filter(ClothesRequest.id == int(rec.ref_id)).first()
        if cr:
            cr.received_by = rec.guard_name
    _notify(db, [rec.guard_id], "Custody handed to you",
            "Open 'My Custody' to confirm what you received.", rec.disbursement_id)
    db.commit()
    db.refresh(rec)
    return _to_dict(rec)


# ── Guard: pending / history / confirm / dispute ─────────────────────────────

@router.get("/my-pending", summary="Guard: items awaiting my confirmation")
def guard_pending(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    recs = db.query(CustodyDisbursement).filter(
        CustodyDisbursement.guard_id == current_user.user_id,
        CustodyDisbursement.status == "handed",
        CustodyDisbursement.is_active == True,
    ).order_by(CustodyDisbursement.created_at.desc()).all()
    return [_to_dict(r) for r in recs]


@router.get("/my-history", summary="Guard: my custody history")
def guard_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    recs = db.query(CustodyDisbursement).filter(
        CustodyDisbursement.guard_id == current_user.user_id,
        CustodyDisbursement.is_active == True,
        CustodyDisbursement.status.in_(["handed", "confirmed", "disputed"]),
    ).order_by(CustodyDisbursement.created_at.desc()).all()
    return [_to_dict(r) for r in recs]


@router.post("/{disbursement_id}/confirm", summary="Guard confirms receipt")
async def guard_confirm(
    disbursement_id: str,
    guard_confirmed_amount: Optional[float] = Form(None),
    guard_notes: Optional[str] = Form(None),
    photo: Optional[UploadFile] = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rec = _get_record(db, disbursement_id)
    if rec.guard_id != current_user.user_id:
        raise HTTPException(404, "Disbursement not found")
    if rec.status != "handed":
        raise HTTPException(400, f"Cannot confirm in status: {rec.status}")
    if rec.custody_type in CASH_TYPES and guard_confirmed_amount is None:
        raise HTTPException(400, "Please enter the amount you received")

    photo_url = None
    if photo and photo.filename:
        ext = os.path.splitext(photo.filename)[1] or ".jpg"
        fname = f"{disbursement_id}_{uuid.uuid4().hex[:8]}{ext}"
        with open(os.path.join(PHOTO_DIR, fname), "wb") as f:
            shutil.copyfileobj(photo.file, f)
        photo_url = f"/static/uploads/{PHOTO_SUBDIR}/{fname}"

    rec.guard_confirmed_amount = guard_confirmed_amount
    rec.guard_photo_url = photo_url
    rec.guard_notes = guard_notes
    rec.status = "confirmed"
    rec.confirmed_at = _now()
    rec.updated_at = _now()
    if rec.ref_id and rec.ref_id.isdigit():
        from app.models.clothes_inventory import ClothesRequest
        cr = db.query(ClothesRequest).filter(ClothesRequest.id == int(rec.ref_id)).first()
        if cr:
            cr.status = "taken"

    _notify(db, [rec.supervisor_id], "Guard confirmed receipt",
            f"{rec.guard_name} confirmed {rec.custody_type}", rec.disbursement_id)
    if (rec.amount is not None and guard_confirmed_amount is not None
            and abs(rec.amount - guard_confirmed_amount) > 0.01):
        _notify(db, _users_with_role(db, UserRole.ADMIN, UserRole.ACCOUNTANT),
                "Custody amount mismatch",
                f"{rec.guard_name}: expected {rec.amount:.2f}, received {guard_confirmed_amount:.2f}",
                rec.disbursement_id)
    db.commit()
    db.refresh(rec)
    return _to_dict(rec)


@router.post("/{disbursement_id}/dispute", summary="Guard disputes receipt")
def guard_dispute(
    disbursement_id: str,
    guard_notes: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rec = _get_record(db, disbursement_id)
    if rec.guard_id != current_user.user_id:
        raise HTTPException(404, "Disbursement not found")
    if rec.status != "handed":
        raise HTTPException(400, f"Cannot dispute in status: {rec.status}")

    rec.status = "disputed"
    rec.guard_notes = guard_notes
    rec.updated_at = _now()
    _notify(db, [rec.supervisor_id] + _users_with_role(db, UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.HR),
            "Custody disputed", f"{rec.guard_name}: {guard_notes}", rec.disbursement_id)
    db.commit()
    db.refresh(rec)
    return _to_dict(rec)


@router.post("/{disbursement_id}/resolve", summary="Resolve a disputed custody")
def resolve_dispute(
    disbursement_id: str,
    data: ResolveBody,
    current_user: User = Depends(require_role(
        UserRole.ADMIN, UserRole.CEO, UserRole.ACCOUNTANT, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """reopen → back to supervisor hand-off queue; cancel → closes it (credit refunded)."""
    rec = _get_record(db, disbursement_id)
    if rec.status != "disputed":
        raise HTTPException(400, f"Cannot resolve in status: {rec.status}")
    if data.action not in ("reopen", "cancel"):
        raise HTTPException(400, "action must be 'reopen' or 'cancel'")

    if data.action == "reopen":
        rec.status = "pending_handoff"
        rec.handed_at = None
        rec.guard_confirmed_amount = None
        rec.guard_photo_url = None
        rec.supervisor_id = None if rec.custody_type in CASH_TYPES else rec.supervisor_id
        rec.supervisor_name = None if rec.custody_type in CASH_TYPES else rec.supervisor_name
    else:
        rec.status = "cancelled"
        if (rec.credit_deducted or 0) > 0 and rec.transfer_method_id:
            from app.models.transfer_method_credit import TransferMethodCredit, TransferMethodCreditLog
            credit = db.query(TransferMethodCredit).filter(
                TransferMethodCredit.transfer_method_id == rec.transfer_method_id).first()
            if credit:
                now = _now()
                credit.balance = round(credit.balance + rec.credit_deducted, 2)
                credit.total_deducted = round(max(credit.total_deducted - rec.credit_deducted, 0), 2)
                credit.updated_at = now
                db.add(TransferMethodCreditLog(
                    id=str(uuid.uuid4()), credit_id=credit.id, operation="refund",
                    amount=rec.credit_deducted, balance_after=credit.balance,
                    reference=f"Custody cancelled — {rec.guard_name}",
                    actor_id=current_user.user_id, actor_name=current_user.name, created_at=now,
                ))
                rec.credit_deducted = 0.0

    rec.approval_notes = ((rec.approval_notes or "") + f"\n[resolve:{data.action}] {data.notes or ''}").strip()
    rec.updated_at = _now()
    _notify(db, [rec.guard_id, rec.supervisor_id, rec.requested_by], "Custody dispute resolved",
            f"{rec.guard_name}: {data.action}", rec.disbursement_id)
    db.commit()
    db.refresh(rec)
    return _to_dict(rec)


# ── Management views (admin / hr / accountant / personnel / ops / ceo) ───────

MANAGE_ROLES = (UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.HR, UserRole.PERSONNEL_OFFICER,
                UserRole.OPERATIONS_MANAGER, UserRole.CEO)


@router.get("/admin/all", summary="Management: list custody records")
def admin_view_all(
    custody_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="comma separated"),
    site_id: Optional[str] = Query(None),
    guard_badge: Optional[str] = Query(None),
    current_user: User = Depends(require_role(*MANAGE_ROLES)),
    db: Session = Depends(get_db),
):
    q = db.query(CustodyDisbursement).filter(CustodyDisbursement.is_active == True)
    if custody_type:
        q = q.filter(CustodyDisbursement.custody_type == custody_type)
    if status:
        q = q.filter(CustodyDisbursement.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    if site_id:
        q = q.filter(CustodyDisbursement.site_id == site_id)
    if guard_badge:
        q = q.filter(or_(CustodyDisbursement.guard_badge.like(f"%{guard_badge}%"),
                         CustodyDisbursement.guard_name.like(f"%{guard_badge}%")))
    return [_to_dict(r) for r in q.order_by(CustodyDisbursement.created_at.desc()).limit(500).all()]


@router.get("/admin/stats", summary="Management: counts per status")
def admin_stats(
    current_user: User = Depends(require_role(*MANAGE_ROLES)),
    db: Session = Depends(get_db),
):
    rows = db.query(CustodyDisbursement.status).filter(CustodyDisbursement.is_active == True).all()
    counts: dict = {}
    for (st,) in rows:
        counts[st] = counts.get(st, 0) + 1
    return counts


# ── Helper lookups for the create forms ──────────────────────────────────────

@router.get("/guards", summary="Search guards by name or badge")
def search_guards(
    q: str = Query("", description="name or badge"),
    current_user: User = Depends(require_role(
        UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.HR, UserRole.PERSONNEL_OFFICER)),
    db: Session = Depends(get_db),
):
    query = db.query(User).filter(User.role.in_(GUARD_ROLES), User.is_active == True)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(User.name.like(like), User.badge_number.like(like)))
    users = query.order_by(User.name).limit(30).all()
    return [{"user_id": u.user_id, "name": u.name, "badge_number": u.badge_number} for u in users]


@router.get("/supervisors", summary="List supervisors / leaders (for issuing items)")
def list_supervisors(
    current_user: User = Depends(require_role(
        UserRole.ADMIN, UserRole.HR, UserRole.PERSONNEL_OFFICER)),
    db: Session = Depends(get_db),
):
    users = db.query(User).filter(
        User.role.in_([UserRole.SUPERVISOR.value, UserRole.LEADER.value]),
        User.is_active == True,
    ).order_by(User.name).all()
    return [{"user_id": u.user_id, "name": u.name, "badge_number": u.badge_number,
             "role": _role(u)} for u in users]


# ── Candidates: records from existing systems not yet in custody ─────────────

@router.get("/candidates/salary", summary="Accountant: cash salaries from payroll not yet requested")
def salary_candidates(
    year: int = Query(...),
    month: int = Query(...),
    current_user: User = Depends(require_role(UserRole.ACCOUNTANT, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    from app.models.payroll_sheet_row import PayrollSheetRow
    rows = db.query(PayrollSheetRow).filter(
        PayrollSheetRow.year == year, PayrollSheetRow.month == month).all()
    if not rows:
        return []

    users = {u.user_id: u for u in db.query(User).filter(
        User.user_id.in_({r.user_id for r in rows if r.user_id})).all()}
    sites_by_name = {s.name: s.site_id for s in db.query(Site).all()}
    ref = f"{year}-{month:02d}"
    done = {e.guard_id for e in db.query(CustodyDisbursement).filter(
        CustodyDisbursement.custody_type == "salary",
        CustodyDisbursement.ref_id == ref,
        CustodyDisbursement.is_active == True,
        CustodyDisbursement.status.notin_(["rejected", "cancelled"]),
    ).all()}

    result = []
    for r in rows:
        u = users.get(r.user_id)
        if not u or r.user_id in done:
            continue
        method = (r.transfer_method or u.transfer_method or "").lower()
        is_cash = any(k in method for k in ("cash", "كاش", "نقد"))
        cash_amt = float(r.cash_payment or 0)
        if not is_cash and cash_amt <= 0:
            continue
        amount = cash_amt if cash_amt > 0 else float(r.net_salary or 0)
        if amount <= 0:
            continue
        result.append({
            "guard_id": r.user_id,
            "guard_name": r.employee_name or u.name,
            "guard_badge": u.badge_number,
            "site_id": sites_by_name.get(r.site_name or ""),
            "site_name": r.site_name,
            "amount": round(amount, 2),
            "ref_id": ref,
            "description": f"Salary {ref}",
        })
    result.sort(key=lambda x: (x["site_name"] or "", x["guard_name"] or ""))
    return result


@router.get("/candidates/advances", summary="Accountant: approved cash advances not yet requested")
def advance_candidates(
    current_user: User = Depends(require_role(UserRole.ACCOUNTANT, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    from app.models.cash_advance import CashAdvance
    advances = db.query(CashAdvance).filter(
        CashAdvance.status.in_(["admin_approved", "admin_modified", "ceo_approved"])
    ).order_by(CashAdvance.created_at.desc()).limit(300).all()

    linked = {e.ref_id for e in db.query(CustodyDisbursement).filter(
        CustodyDisbursement.custody_type == "cash_advance",
        CustodyDisbursement.is_active == True,
        CustodyDisbursement.status.notin_(["rejected", "cancelled"]),
    ).all()}

    result = []
    for a in advances:
        if a.advance_id in linked:
            continue
        amount = a.approved_amount if a.approved_amount else a.amount
        result.append({
            "guard_id": a.guard_id,
            "guard_name": a.guard_name,
            "guard_badge": a.guard_code,
            "site_id": a.site_id,
            "site_name": a.site_name,
            "amount": round(float(amount or 0), 2),
            "ref_id": a.advance_id,
            "description": f"Cash advance ({a.installment_months} month(s))",
        })
    return result


@router.get("/candidates/clothes", summary="Personnel: HR-approved clothes requests not yet issued")
def clothes_candidates(
    current_user: User = Depends(require_role(UserRole.PERSONNEL_OFFICER, UserRole.HR, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    from app.models.clothes_inventory import ClothesRequest
    reqs = db.query(ClothesRequest).filter(
        ClothesRequest.status == "approved", ClothesRequest.user_id != None
    ).order_by(ClothesRequest.request_date.desc()).limit(300).all()

    linked = {e.ref_id for e in db.query(CustodyDisbursement).filter(
        CustodyDisbursement.custody_type == "clothes",
        CustodyDisbursement.is_active == True,
        CustodyDisbursement.status.notin_(["rejected", "cancelled"]),
    ).all()}
    sites_by_name = {s.name: s.site_id for s in db.query(Site).all()}

    result = []
    for r in reqs:
        if str(r.id) in linked:
            continue
        cats = r.categories if isinstance(r.categories, dict) else {}
        items = ", ".join(f"{k} x{v}" for k, v in cats.items() if v)
        result.append({
            "guard_id": r.user_id,
            "guard_name": r.user_name,
            "guard_badge": r.user_code,
            "site_id": sites_by_name.get(r.site_name or ""),
            "site_name": r.site_name,
            "amount": None,
            "ref_id": str(r.id),
            "description": (r.reason or "Uniform") + (f" — {items}" if items else ""),
        })
    return result
