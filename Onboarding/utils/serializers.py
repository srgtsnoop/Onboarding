"""JSON serialization helpers for API-style endpoints."""
from __future__ import annotations

from Onboarding.models import User, Week
from Onboarding.utils.plan_service import weeks_for_plan


def serialize_week(week: Week):
    return {
        "id": week.id,
        "title": week.title,
        "start_date": week.start_date.isoformat() if week.start_date else None,
        "end_date": week.end_date.isoformat() if week.end_date else None,
        "tasks": [t.id for t in week.tasks],
    }


def serialize_user_with_plan(user: User):
    plan = user.onboarding_plan
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "plan": (
            None
            if not plan
            else {
                "id": plan.id,
                "name": plan.name,
                "weeks": [serialize_week(w) for w in weeks_for_plan(plan.id)],
            }
        ),
    }
