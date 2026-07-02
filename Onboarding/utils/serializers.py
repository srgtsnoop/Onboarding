from Onboarding.models import Week, User, OnboardingPlan


def serialize_week(week: Week) -> dict:
    return {
        "id": week.id,
        "title": week.title,
        "start_date": week.start_date.isoformat() if week.start_date else None,
        "end_date": week.end_date.isoformat() if week.end_date else None,
        "tasks": [t.id for t in week.tasks],
    }


def serialize_user_with_plan(user: User) -> dict:
    from Onboarding.utils.plan_service import weeks_for_plan

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
