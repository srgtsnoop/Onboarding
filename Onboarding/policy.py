"""Access policy helpers for onboarding data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from flask import abort, g, request
from sqlalchemy import false, or_

from Onboarding.models import Week


@dataclass
class Principal:
    """Represents the caller making a request."""

    user_id: Optional[int]
    role: str

    def normalized_role(self) -> str:
        return (self.role or "").lower()


def _parse_int(value: str | None) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def get_current_principal() -> Principal:
    """Build a principal from request headers (cached on ``g``).

    Defaults to a regular user if no role header is provided.
    """

    if hasattr(g, "principal"):
        return g.principal  # type: ignore[attr-defined]

    role = request.headers.get("X-User-Role", "user")
    user_id = _parse_int(request.headers.get("X-User-Id"))

    principal = Principal(user_id=user_id, role=role)
    g.principal = principal
    return principal


def can_access_week(principal: Principal, week: Week) -> bool:
    role = principal.normalized_role()

    if role == "admin":
        return True

    if principal.user_id is None:
        return False

    if role == "manager":
        return (
            week.manager_user_id == principal.user_id
            or week.owner_user_id == principal.user_id
        )

    if role == "user":
        return week.owner_user_id == principal.user_id

    return False


def ensure_week_access(principal: Principal, week: Week) -> None:
    if not can_access_week(principal, week):
        abort(403)


def filter_weeks_for_principal(principal: Principal):
    role = principal.normalized_role()

    if role == "admin":
        return Week.query

    if principal.user_id is None:
        return Week.query.filter(false())

    if role == "manager":
        return Week.query.filter(
            or_(
                Week.manager_user_id == principal.user_id,
                Week.owner_user_id == principal.user_id,
            )
        )

    if role == "user":
        return Week.query.filter(Week.owner_user_id == principal.user_id)

    return Week.query.filter(false())
