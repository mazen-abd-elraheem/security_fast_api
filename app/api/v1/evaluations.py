"""
SecureTrack — Guard Evaluations API
Supervisors evaluate guards; HR/Admin can view all evaluations.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.guard_evaluation import GuardEvaluation
from app.core.audit import log_audit, log_create, log_update, log_delete, log_read, snapshot

router = APIRouter()


class EvalCreate(BaseModel):
    guard_id: str
    period: str
    attendance_score: int = Field(..., ge=1, le=5)
    punctuality_score: int = Field(..., ge=1, le=5)
    appearance_score: int = Field(..., ge=1, le=5)
    discipline_score: int = Field(..., ge=1, le=5)
    communication_score: int = Field(..., ge=1, le=5)
    comments: Optional[str] = None


def _eval_dict(e: GuardEvaluation) -> dict:
    return {
        "eval_id": e.eval_id,
        "guard_id": e.guard_id,
        "guard_name": e.guard_name,
        "evaluator_id": e.evaluator_id,
        "evaluator_name": e.evaluator_name,
        "period": e.period,
        "attendance_score": e.attendance_score,
        "punctuality_score": e.punctuality_score,
        "appearance_score": e.appearance_score,
        "discipline_score": e.discipline_score,
        "communication_score": e.communication_score,
        "overall_score": e.overall_score,
        "comments": e.comments,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Submit guard evaluation")
def create_evaluation(
    body: EvalCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    guard = db.query(User).filter(User.user_id == body.guard_id).first()
    if not guard:
        raise HTTPException(status_code=404, detail="Guard not found")

    scores = [body.attendance_score, body.punctuality_score, body.appearance_score,
              body.discipline_score, body.communication_score]
    overall = round(sum(scores) / len(scores), 2)

    ev = GuardEvaluation(
        eval_id=str(uuid.uuid4()),
        guard_id=body.guard_id,
        guard_name=guard.name,
        evaluator_id=current_user.user_id,
        evaluator_name=current_user.name,
        period=body.period,
        attendance_score=body.attendance_score,
        punctuality_score=body.punctuality_score,
        appearance_score=body.appearance_score,
        discipline_score=body.discipline_score,
        communication_score=body.communication_score,
        overall_score=overall,
        comments=body.comments,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return _eval_dict(ev)


@router.get("/", summary="List evaluations")
def list_evaluations(
    guard_id: Optional[str] = Query(None),
    period: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(GuardEvaluation)
    if guard_id:
        query = query.filter(GuardEvaluation.guard_id == guard_id)
    if period:
        query = query.filter(GuardEvaluation.period == period)
    if current_user.role == "supervisor":
        query = query.filter(GuardEvaluation.evaluator_id == current_user.user_id)

    total = query.count()
    evals = query.order_by(GuardEvaluation.created_at.desc()).offset(skip).limit(limit).all()
    return {"total": total, "evaluations": [_eval_dict(e) for e in evals]}


@router.get("/{eval_id}", summary="Get evaluation details")
def get_evaluation(
    eval_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ev = db.query(GuardEvaluation).filter(GuardEvaluation.eval_id == eval_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return _eval_dict(ev)


@router.get("/summary/all", summary="Get aggregated evaluations summary for admin/ops")
def get_evaluations_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in ["admin", "operations_manager"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    from app.models.guard_roster import GuardRoster
    from app.models.daily_attendance_entry import DailyAttendanceEntry
    from app.models.site import Site
    from app.models.shift import Shift
    from sqlalchemy.orm import aliased

    # Target users: guards and supervisors
    users = db.query(User).filter(User.role.in_(["guard", "supervisor", "lady"]), User.is_active == True).all()

    results = []
    for u in users:
        # 1. Latest assigned site
        latest_roster = db.query(GuardRoster).join(Shift).filter(
            GuardRoster.guard_id == u.user_id,
            GuardRoster.status != "canceled"
        ).order_by(GuardRoster.assigned_date.desc()).first()

        site_name = "N/A"
        supervisor_name = "N/A"

        if latest_roster and latest_roster.shift:
            site = db.query(Site).filter(Site.site_id == latest_roster.shift.site_id).first()
            if site:
                site_name = site.name
            
            # Find the supervisor for this site
            if u.role in ["guard", "lady"]:
                # Look up any supervisor assigned to this site
                sup_roster = db.query(GuardRoster).join(Shift).join(User).filter(
                    Shift.site_id == latest_roster.shift.site_id,
                    User.role == "supervisor",
                    GuardRoster.status != "canceled"
                ).order_by(GuardRoster.assigned_date.desc()).first()
                
                if sup_roster and sup_roster.guard:
                    supervisor_name = sup_roster.guard.name

        # 2. Absent days (sum of absence_unexcused, absence_excused, annual_leave, sick_leave)
        absent_days = db.query(DailyAttendanceEntry).filter(
            DailyAttendanceEntry.employee_id == u.user_id,
            DailyAttendanceEntry.status.in_(["absence_unexcused", "absence_excused", "annual_leave", "sick_leave"])
        ).count()

        # 3. Latest evaluation score
        latest_eval = db.query(GuardEvaluation).filter(
            GuardEvaluation.guard_id == u.user_id
        ).order_by(GuardEvaluation.created_at.desc()).first()
        
        score = latest_eval.overall_score if latest_eval else 0.0

        results.append({
            "employee_code": u.employee_code,
            "name": u.name,
            "role": u.role,
            "site_name": site_name,
            "supervisor_name": supervisor_name,
            "absent_days": absent_days,
            "evaluation_score": score,
        })
        
    return results
