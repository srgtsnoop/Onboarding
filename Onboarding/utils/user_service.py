from flask import request, abort
from sqlalchemy.orm import selectinload
from Onboarding.extensions import db
from Onboarding.models import User, RoleEnum, Week, Task
from Onboarding.policy import Principal, get_current_principal


def current_user() -> User:
    email = request.headers.get("X-User-Email") or request.args.get("as_user")
    query = User.query.options(selectinload(User.onboarding_plan))
    if email:
        user = query.filter_by(email=email).first()
        if not user:
            abort(404, description="User not found")
        return user
    user = query.order_by(User.id.asc()).first()
    if not user:
        abort(404, description="No users available")
    return user


def resolve_principal(user: User) -> Principal:
    if "X-User-Role" in request.headers or "X-User-Id" in request.headers:
        return get_current_principal()
    return Principal(user_id=user.id, role=user.role)


def require_role(user: User, allowed_roles: set[str]):
    if user.role not in allowed_roles:
        abort(403)


def require_builder_or_admin(user: User):
    require_role(user, {RoleEnum.BUILDER.value, RoleEnum.ADMIN.value})


def require_manager_or_admin(user: User):
    allowed = {RoleEnum.MANAGER.value, RoleEnum.ADMIN.value}
    if hasattr(RoleEnum, "BUILDER"):
        allowed.add(RoleEnum.BUILDER.value)
    require_role(user, allowed)


def ensure_week_for_user(week_id: int, user: User) -> Week:
    week = (
        Week.query.filter_by(id=week_id, onboarding_plan_id=user.onboarding_plan_id)
        .options(selectinload(Week.tasks))
        .first()
    )
    if not week:
        abort(404)
    return week


def ensure_task_for_user(task_id: int, user: User) -> Task:
    task = Task.query.options(selectinload(Task.week)).get(task_id)
    if not task or task.week.onboarding_plan_id != user.onboarding_plan_id:
        abort(404)
    return task
