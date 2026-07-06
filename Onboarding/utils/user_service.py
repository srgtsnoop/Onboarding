"""User identity resolution and role/access enforcement.

The acting user is resolved from (in order): the X-User-Email header, the
``as_user`` query parameter, or the session; falling back to the first user
in the database so the dev UI works without explicit context.

NOTE: this is a development identity scheme, not authentication. Replace
with a real login system before production use.
"""
from __future__ import annotations

from flask import abort, request, session
from sqlalchemy.orm import selectinload

from Onboarding.extensions import db
from Onboarding.models import RoleEnum, Task, User, Week
from Onboarding.policy import Principal, ensure_week_access, get_current_principal


def has_header_principal() -> bool:
    return "X-User-Role" in request.headers or "X-User-Id" in request.headers


def current_user() -> User:
    """Resolve the acting user from a header, query param, or session."""
    email = (
        request.headers.get("X-User-Email")
        or request.args.get("as_user")
        or session.get("as_user")
    )
    query = User.query.options(selectinload(User.onboarding_plan))

    if email:
        user = query.filter_by(email=email).first()
        if not user:
            # Stale session value (e.g. reseeded DB): clear it and fall through.
            if session.get("as_user") == email and not (
                request.headers.get("X-User-Email") or request.args.get("as_user")
            ):
                session.pop("as_user", None)
            else:
                abort(404, description="User not found")
        else:
            # Persist the selection so navigation keeps the chosen identity.
            if request.args.get("as_user"):
                session["as_user"] = email
            return user

    user = query.order_by(User.id.asc()).first()
    if not user:
        abort(404, description="No users available")
    return user


def optional_current_user() -> User | None:
    """Like current_user() but returns None instead of aborting."""
    try:
        return current_user()
    except Exception:
        return None


def resolve_principal(user: User) -> Principal:
    """Prefer an explicit header principal, otherwise derive from ``user``."""
    if has_header_principal():
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


def require_admin(user: User):
    require_role(user, {RoleEnum.ADMIN.value})


def ensure_week_for_user(week_id: int, user: User) -> Week:
    """Fetch a week by id and authorize it against the acting user.

    Authorization goes through ``ensure_week_access`` (owner, direct
    manager, or admin) rather than requiring the week's plan to belong
    to ``user`` directly - that's what lets a manager open a direct
    report's week without impersonating them via ``as_user``.
    """
    week = db.session.get(Week, week_id, options=[selectinload(Week.tasks)])
    if not week:
        abort(404)
    ensure_week_access(resolve_principal(user), week)
    return week


def ensure_task_for_user(task_id: int, user: User) -> Task:
    task = db.session.get(Task, task_id, options=[selectinload(Task.week)])
    if not task:
        abort(404)
    ensure_week_access(resolve_principal(user), task.week)
    return task
