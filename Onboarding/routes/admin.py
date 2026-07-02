from __future__ import annotations
from sqlalchemy.orm import selectinload
from flask import Blueprint, render_template, redirect, url_for, request, flash
from Onboarding.extensions import db
from Onboarding.models import User, RoleEnum, Week, Task, StatusEnum, OnboardingPlan
from Onboarding.utils.user_service import current_user, require_role
from Onboarding.utils.serializers import serialize_user_with_plan

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
    return render_template("admin_overview.html", admin=admin, users=users, plans=plans)


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
        "admin": {"id": admin.id, "email": admin.email, "full_name": admin.full_name},
        "users": [serialize_user_with_plan(u) | {"manager_id": u.manager_id} for u in users],
        "plans": [
            {"id": p.id, "name": p.name, "week_ids": [w.id for w in p.weeks]}
            for p in plans
        ],
    }


@bp.route("/admin/users/<int:user_id>/plan/delete", methods=["GET", "POST"])
def admin_delete_user_plan(user_id: int):
    admin = current_user()
    require_role(admin, {RoleEnum.ADMIN.value})
    target_user = User.query.get_or_404(user_id)
    plan = target_user.onboarding_plan
    if not plan:
        return redirect(url_for("admin.admin_overview"))
    started_tasks_count = (
        Task.query.join(Week)
        .filter(
            Week.onboarding_plan_id == plan.id,
            Task.status.in_([StatusEnum.IN_PROGRESS.value, StatusEnum.COMPLETE.value]),
        )
        .count()
    )
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
                    target_user=target_user,
                    plan=plan,
                    started_tasks_count=started_tasks_count,
                    error="Name does not match. Please try again.",
                )
        target_user.onboarding_plan_id = None
        db.session.delete(plan)
        db.session.commit()
        flash(f"Onboarding plan for {target_user.full_name} has been removed.", "success")
        return redirect(url_for("admin.admin_overview"))
    return render_template(
        "admin_delete_plan_confirm.html",
        admin=admin,
        target_user=target_user,
        plan=plan,
        started_tasks_count=started_tasks_count,
    )
