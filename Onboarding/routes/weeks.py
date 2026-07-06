"""Weeks pages, plan API, health check."""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, render_template, request
from sqlalchemy.orm import selectinload

from Onboarding.extensions import db
from Onboarding.models import RoleEnum, Task, User, Week
from Onboarding.policy import (
    ensure_week_access,
    filter_weeks_for_principal,
    get_current_principal,
)
from Onboarding.utils.plan_service import week_progress, weeks_for_plan
from Onboarding.utils.serializers import serialize_user_with_plan
from Onboarding.utils.user_service import (
    current_user,
    ensure_week_for_user,
    has_header_principal,
    optional_current_user,
)

bp = Blueprint("weeks", __name__)


@bp.get("/weeks")
def weeks():
    user = optional_current_user()
    viewing_user = user
    viewing_as_manager = False

    if has_header_principal():
        # API/header-driven access: honor the role-based access policy.
        principal = get_current_principal()
        plan_weeks = (
            filter_weeks_for_principal(principal)
            .options(selectinload(Week.tasks))
            .order_by(Week.start_date.asc().nullsfirst(), Week.id.asc())
            .all()
        )
    else:
        if user is None:
            abort(404, description="No users available")

        # A manager (or admin) can look at a direct report's plan without
        # taking over their session identity - see view_user_id, not as_user.
        view_user_id = request.args.get("view_user_id", type=int)
        if view_user_id and view_user_id != user.id:
            viewing_user = db.get_or_404(
                User, view_user_id, options=[selectinload(User.onboarding_plan)]
            )
            if not (
                user.role == RoleEnum.ADMIN.value
                or viewing_user.manager_id == user.id
            ):
                abort(403)
            viewing_as_manager = True

        plan_weeks = weeks_for_plan(viewing_user.onboarding_plan_id)

    progress = {w.id: week_progress(w) for w in plan_weeks}
    totals = {
        "total": sum(p["total"] for p in progress.values()),
        "done": sum(p["done"] for p in progress.values()),
        "overdue": sum(p["overdue"] for p in progress.values()),
    }
    totals["percent"] = (
        int(round(totals["done"] / totals["total"] * 100)) if totals["total"] else 0
    )

    return render_template(
        "weeks.html",
        weeks=plan_weeks,
        user=user or {"role": "guest"},
        viewing_user=viewing_user or {"role": "guest"},
        viewing_as_manager=viewing_as_manager,
        progress=progress,
        totals=totals,
    )


@bp.get("/weeks/<int:week_id>")
def week_detail(week_id: int):
    user = optional_current_user()

    if has_header_principal():
        w = db.session.get(Week, week_id, options=[selectinload(Week.tasks)])
        if not w:
            abort(404)
        ensure_week_access(get_current_principal(), w)
    else:
        if user is None:
            abort(404, description="No users available")
        w = ensure_week_for_user(week_id, user)

    tasks = (
        Task.query.filter_by(week_id=w.id)
        .order_by(Task.sort_order.asc(), Task.id.asc())
        .all()
    )
    return render_template(
        "week_detail.html",
        w=w,
        tasks=tasks,
        user=user or {"role": "guest"},
        progress=week_progress(w),
    )


@bp.get("/api/my-plan")
def api_my_plan():
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
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}


@bp.get("/debug/db")
def debug_db():
    user = optional_current_user()
    if not user or user.role != RoleEnum.ADMIN.value:
        abort(403)
    return {
        "db_path": current_app.config["SQLALCHEMY_DATABASE_URI"],
        "weeks_count": Week.query.count(),
        "tasks_count": Task.query.count(),
    }
