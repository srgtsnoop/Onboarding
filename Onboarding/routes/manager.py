from __future__ import annotations
from datetime import date, datetime
from sqlalchemy.orm import selectinload
from flask import Blueprint, render_template, redirect, url_for, request, abort
from Onboarding.extensions import db
from Onboarding.models import (
    User,
    RoleEnum,
    Week,
    Task,
    StatusEnum,
    OnboardingPlan,
    OnboardingTemplate,
    TemplateStatusEnum,
)
from Onboarding.utils.user_service import current_user, require_role, require_manager_or_admin
from Onboarding.utils.plan_service import create_plan_from_template
from Onboarding.utils.serializers import serialize_user_with_plan

bp = Blueprint("manager", __name__)


@bp.get("/manager/reports")
def manager_reports():
    manager = current_user()
    require_role(manager, {RoleEnum.MANAGER.value, RoleEnum.ADMIN.value})
    reports = (
        User.query.filter(User.manager_id == manager.id)
        .options(
            selectinload(User.onboarding_plan)
            .selectinload(OnboardingPlan.weeks)
            .selectinload(Week.tasks),
        )
        .order_by(User.full_name.asc())
        .all()
    )
    return render_template("manager_reports.html", manager=manager, reports=reports)


@bp.get("/api/manager/reports")
def api_manager_reports():
    manager = current_user()
    require_role(manager, {RoleEnum.MANAGER.value, RoleEnum.ADMIN.value})
    reports = (
        User.query.filter(User.manager_id == manager.id)
        .options(selectinload(User.onboarding_plan).selectinload(OnboardingPlan.weeks))
        .order_by(User.id.asc())
        .all()
    )
    return {
        "manager": {"id": manager.id, "email": manager.email, "full_name": manager.full_name},
        "direct_reports": [serialize_user_with_plan(u) for u in reports],
    }


@bp.get("/manager/plans")
def manager_plans():
    user = current_user()
    require_manager_or_admin(user)
    employees = (
        User.query.filter(User.onboarding_plan_id.isnot(None))
        .order_by(User.full_name.asc())
        .all()
    )
    summaries = []
    for emp in employees:
        plan = OnboardingPlan.query.get(emp.onboarding_plan_id)
        if not plan:
            continue
        weeks = Week.query.filter_by(onboarding_plan_id=plan.id).all()
        week_ids = [w.id for w in weeks]
        total_tasks = 0
        complete_tasks = 0
        if week_ids:
            total_tasks = Task.query.filter(Task.week_id.in_(week_ids)).count()
            complete_tasks = Task.query.filter(
                Task.week_id.in_(week_ids),
                Task.status == StatusEnum.COMPLETE.value,
            ).count()
        summaries.append(
            {
                "employee": emp,
                "plan": plan,
                "total_tasks": total_tasks,
                "complete_tasks": complete_tasks,
                "progress_pct": int((complete_tasks / total_tasks) * 100) if total_tasks else 0,
            }
        )
    return render_template("manager_plans.html", user=user, summaries=summaries)


@bp.get("/assign")
def assign_plan_form():
    user = current_user()
    require_manager_or_admin(user)
    templates = (
        OnboardingTemplate.query.filter_by(status=TemplateStatusEnum.PUBLISHED.value)
        .order_by(OnboardingTemplate.name.asc())
        .all()
    )
    employees = User.query.order_by(User.full_name.asc()).all()
    return render_template(
        "assign_plan.html",
        user=user,
        templates=templates,
        employees=employees,
        today=date.today().isoformat(),
    )


@bp.post("/assign")
def assign_plan():
    user = current_user()
    require_manager_or_admin(user)
    try:
        template_id = int(request.form.get("template_id") or "")
        employee_id = int(request.form.get("user_id") or "")
    except ValueError:
        return redirect(url_for("manager.assign_plan_form"))
    tpl = OnboardingTemplate.query.get_or_404(template_id)
    if tpl.status != TemplateStatusEnum.PUBLISHED.value:
        abort(400, description="Template must be published to assign a plan.")
    employee = User.query.get_or_404(employee_id)
    start_date = date.today()
    start_raw = (request.form.get("start_date") or "").strip()
    if start_raw:
        try:
            start_date = datetime.strptime(start_raw, "%Y-%m-%d").date()
        except ValueError:
            pass
    plan, weeks = create_plan_from_template(tpl.id, employee, start_date)
    first_week = None
    if weeks:
        first_week = sorted(weeks, key=lambda w: (w.start_date or date.max, w.id))[0]
    if first_week:
        return redirect(url_for("weeks.week_detail", week_id=first_week.id, as_user=employee.email))
    return redirect(url_for("weeks.weeks"))
