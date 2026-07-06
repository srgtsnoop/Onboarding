"""Admin pages: org overview and plan deletion."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy.orm import selectinload

from Onboarding.extensions import db
from Onboarding.models import (
    OnboardingPlan,
    RoleEnum,
    StatusEnum,
    Task,
    User,
    Week,
)
from Onboarding.utils.serializers import serialize_user_with_plan
from Onboarding.utils.user_service import current_user, require_role

bp = Blueprint("admin", __name__)


@bp.get("/admin/overview")
def admin_overview():
    admin = current_user()
    require_role(admin, {RoleEnum.ADMIN.value})

    users = (
        User.query.options(
            selectinload(User.onboarding_plan).selectinload(OnboardingPlan.weeks),
            selectinload(User.manager),
        )
        .order_by(User.full_name.asc())
        .all()
    )

    plans = (
        OnboardingPlan.query.options(selectinload(OnboardingPlan.weeks))
        .order_by(OnboardingPlan.name.asc())
        .all()
    )

    return render_template(
        "admin_overview.html",
        admin=admin,
        user=admin,
        users=users,
        plans=plans,
    )


@bp.get("/api/admin/overview")
def api_admin_overview():
    admin = current_user()
    require_role(admin, {RoleEnum.ADMIN.value})

    users = (
        User.query.options(
            selectinload(User.onboarding_plan).selectinload(OnboardingPlan.weeks),
            selectinload(User.manager),
        )
        .order_by(User.id.asc())
        .all()
    )

    plans = (
        OnboardingPlan.query.options(selectinload(OnboardingPlan.weeks))
        .order_by(OnboardingPlan.id.asc())
        .all()
    )

    return {
        "admin": {
            "id": admin.id,
            "email": admin.email,
            "full_name": admin.full_name,
        },
        "users": [
            serialize_user_with_plan(u) | {"manager_id": u.manager_id} for u in users
        ],
        "plans": [
            {
                "id": p.id,
                "name": p.name,
                "week_ids": [w.id for w in p.weeks],
            }
            for p in plans
        ],
    }


@bp.route("/admin/users/<int:user_id>/plan/delete", methods=["GET", "POST"])
def admin_delete_user_plan(user_id: int):
    admin = current_user()
    require_role(admin, {RoleEnum.ADMIN.value})

    target_user = db.get_or_404(User, user_id)
    plan = target_user.onboarding_plan

    if not plan:
        return redirect(url_for("admin.admin_overview"))

    # Check for started tasks (In Progress or Complete)
    started_tasks_count = (
        Task.query.join(Week)
        .filter(
            Week.onboarding_plan_id == plan.id,
            Task.status.in_([StatusEnum.IN_PROGRESS.value, StatusEnum.COMPLETE.value]),
        )
        .count()
    )

    # HTMX modal trigger returns the modal partial
    if request.headers.get("HX-Request") and request.method == "GET":
        return render_template(
            "admin_delete_plan_modal.html",
            admin=admin,
            target_user=target_user,
            plan=plan,
            started_tasks_count=started_tasks_count,
        )

    if request.method == "POST":
        if started_tasks_count > 0:
            confirm_name = (request.form.get("confirmation_name") or "").strip()
            if confirm_name != target_user.full_name:
                return render_template(
                    "admin_delete_plan_confirm.html",
                    admin=admin,
                    user=admin,
                    target_user=target_user,
                    plan=plan,
                    started_tasks_count=started_tasks_count,
                    error="Name does not match. Please try again.",
                )

        target_user.onboarding_plan_id = None
        db.session.delete(plan)
        db.session.commit()
        flash(
            f"Onboarding plan for {target_user.full_name} has been removed.", "success"
        )
        return redirect(url_for("admin.admin_overview"))

    return render_template(
        "admin_delete_plan_confirm.html",
        admin=admin,
        user=admin,
        target_user=target_user,
        plan=plan,
        started_tasks_count=started_tasks_count,
    )
