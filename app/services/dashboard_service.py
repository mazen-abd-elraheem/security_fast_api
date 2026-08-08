"""
SecureTrack Platform — Dashboard Service
Aggregations for the live admin dashboard.
"""
from typing import Optional
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.user import User
from app.models.site import Site
from app.models.shift import Shift
from app.models.guard_roster import GuardRoster
from app.models.supervisor_route import SupervisorRoute
from app.models.supervisor_visit import SupervisorVisit
from app.models.attendance_log import AttendanceLog
from app.models.incident import Incident


class DashboardService:
    """Aggregations for the admin dashboard."""

    @staticmethod
    def get_live_status(db: Session) -> dict:
        """Get live status overview of all sites."""
        sites = db.query(Site).filter(Site.status == "active").all()
        today = date.today()

        site_items = []
        green = yellow = red = gray = 0

        for site in sites:
            # Count required guards for today
            required = (
                db.query(func.sum(Shift.required_headcount))
                .filter(Shift.site_id == site.site_id, Shift.is_active == True)
                .scalar() or 0
            )

            # Count present guards today
            present = (
                db.query(AttendanceLog)
                .join(SupervisorVisit, AttendanceLog.visit_id == SupervisorVisit.visit_id)
                .filter(
                    SupervisorVisit.site_id == site.site_id,
                    AttendanceLog.status.in_(["present", "late", "replacement"]),
                    AttendanceLog.recorded_at >= datetime.combine(today, datetime.min.time()),
                )
                .count()
            )

            # Last supervisor visit
            last_visit = (
                db.query(SupervisorVisit)
                .filter(SupervisorVisit.site_id == site.site_id)
                .order_by(SupervisorVisit.check_in_time.desc())
                .first()
            )

            # Active incidents
            active_incidents = (
                db.query(Incident)
                .filter(Incident.site_id == site.site_id, Incident.status.in_(["open", "investigating"]))
                .count()
            )

            # Determine color
            coverage_pct = round(present / required * 100, 1) if required > 0 else 0.0
            if required == 0:
                color = "gray"
                gray += 1
            elif coverage_pct >= 90:
                color = "green"
                green += 1
            elif coverage_pct >= 50:
                color = "yellow"
                yellow += 1
            else:
                color = "red"
                red += 1

            site_items.append({
                "site_id": site.site_id,
                "site_name": site.name,
                "region": site.region,
                "required_guards": required,
                "present_guards": present,
                "coverage_percentage": coverage_pct,
                "status_color": color,
                "last_supervisor_visit": last_visit.check_in_time if last_visit else None,
                "has_active_incidents": active_incidents > 0,
            })

        return {
            "timestamp": datetime.now(timezone.utc),
            "total_sites": len(sites),
            "sites_green": green,
            "sites_yellow": yellow,
            "sites_red": red,
            "sites_gray": gray,
            "sites": site_items,
        }

    @staticmethod
    def get_supervisor_progress(db: Session, supervisor_id: str, target_date: date) -> dict:
        """Get a supervisor's route progress for a date."""
        routes = (
            db.query(SupervisorRoute)
            .filter(
                SupervisorRoute.supervisor_id == supervisor_id,
                SupervisorRoute.assigned_date == target_date,
            )
            .all()
        )

        supervisor = db.query(User).filter(User.user_id == supervisor_id).first()
        total = len(routes)
        completed = sum(1 for r in routes if r.status == "completed")
        in_progress = sum(1 for r in routes if r.status == "in_progress")
        pending = sum(1 for r in routes if r.status == "pending")
        skipped = sum(1 for r in routes if r.status == "skipped")

        return {
            "supervisor_id": supervisor_id,
            "supervisor_name": supervisor.name if supervisor else "Unknown",
            "total_assigned": total,
            "completed": completed,
            "in_progress": in_progress,
            "pending": pending,
            "skipped": skipped,
            "progress_percentage": round(completed / total * 100, 1) if total > 0 else 0.0,
        }

    @staticmethod
    def get_platform_stats(db: Session) -> dict:
        """Get overall platform statistics."""
        today = date.today()
        return {
            "total_users": db.query(User).filter(User.is_active == True).count(),
            "total_admins": db.query(User).filter(User.role == "admin", User.is_active == True).count(),
            "total_supervisors": db.query(User).filter(User.role == "supervisor", User.is_active == True).count(),
            "total_guards": db.query(User).filter(User.role == "guard", User.is_active == True).count(),
            "total_sites": db.query(Site).count(),
            "total_active_sites": db.query(Site).filter(Site.status == "active").count(),
            "total_shifts": db.query(Shift).filter(Shift.is_active == True).count(),
            "total_visits_today": (
                db.query(SupervisorVisit)
                .filter(SupervisorVisit.check_in_time >= datetime.combine(today, datetime.min.time()))
                .count()
            ),
            "total_incidents_open": db.query(Incident).filter(Incident.status.in_(["open", "investigating"])).count(),
        }
