from __future__ import annotations
from datetime import datetime
from flask import Blueprint, render_template
from Onboarding.models import Week, Task
from Onboarding.utils.user_service import current_user, ensure_week_for_user
from Onboarding.utils.plan_service import weeks_for_plan
from Onboarding.utils.serializers import serialize_user_with_plan

bp = Blueprint("weeks", __name__)


@bp.get("/weeks")
def weeks():
    user = current_user()
    plan_weeks = weeks_for_plan(user.onboarding_plan_id)
    return render_template("weeks.html", weeks=plan_weeks, user=user)


@bp.get("/weeks/<int:week_id>")
def week_detail(week_id: int):
    user = current_user()
    w = ensure_week_for_user(week_id, user)
    tasks = (
        Task.query.filter_by(week_id=w.id)
        .order_by(Task.sort_order.asc(), Task.id.asc())
        .all()
    )
    return render_template("week_detail.html", w=w, tasks=tasks, user=user)


@bp.get("/api/my-plan")
def my_plan():
    user = current_user()
    serialized = serialize_user_with_plan(user)
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
        },
        "plan": serialized["plan"],
    }


@bp.get("/healthz")
def healthz():
    return {"ok": True, "time": datetime.utcnow().isoformat()}


@bp.get("/debug/db")
def debug_db():
    from flask import current_app
    return {
        "db_path": current_app.config["SQLALCHEMY_DATABASE_URI"],
        "weeks_count": Week.query.count(),
        "tasks_count": Task.query.count(),
    }
