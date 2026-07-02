from datetime import date, timedelta
from sqlalchemy.orm import selectinload
from Onboarding.extensions import db
from Onboarding.models import (
    Week,
    Task,
    StatusEnum,
    User,
    OnboardingPlan,
    OnboardingTemplate,
    TemplateSection,
)


def weeks_for_plan(plan_id: int | None) -> list[Week]:
    if plan_id is None:
        return []
    return (
        Week.query.filter_by(onboarding_plan_id=plan_id)
        .options(selectinload(Week.tasks))
        .order_by(Week.start_date.asc().nullsfirst(), Week.id.asc())
        .all()
    )


def create_plan_from_template(
    template_id: int, employee: User, start_date: date
) -> tuple[OnboardingPlan, list[Week]]:  # ← fixed return type
    tpl = (
        OnboardingTemplate.query.options(
            selectinload(OnboardingTemplate.sections).selectinload(
                TemplateSection.tasks
            )
        )
        .filter_by(id=template_id)
        .first_or_404()
    )

    plan = OnboardingPlan(name=tpl.name, description=tpl.description)
    if hasattr(OnboardingPlan, "template_id"):
        plan.template_id = tpl.id
    if hasattr(OnboardingPlan, "start_date"):
        plan.start_date = start_date

    db.session.add(plan)
    db.session.flush()

    employee.onboarding_plan_id = plan.id

    sections = sorted(
        tpl.sections,
        key=lambda s: ((getattr(s, "order_index", None) or 0), s.id),
    )

    all_weeks = []
    for section in sections:
        offset_days = getattr(section, "offset_days", None) or 0
        week_start = start_date + timedelta(days=offset_days)

        week = Week(
            onboarding_plan_id=plan.id,
            title=section.title,
            start_date=week_start,
            end_date=week_start + timedelta(days=6),
            owner_user_id=employee.id,
            manager_user_id=employee.manager_id,
        )
        db.session.add(week)
        db.session.flush()
        all_weeks.append(week)

        section_tasks = sorted(
            section.tasks,
            key=lambda t: ((getattr(t, "order_index", None) or 0), t.id),
        )

        for sort_idx, tmpl_task in enumerate(section_tasks, start=1):
            due_date = None
            due_type = getattr(tmpl_task, "due_type", None)
            t_offset = getattr(tmpl_task, "offset_days", None)
            section_day = getattr(tmpl_task, "section_day", None)

            if due_type == "days_from_start" and t_offset is not None:
                due_date = start_date + timedelta(days=t_offset)
            elif due_type == "day_within_section" and section_day is not None:
                due_date = week_start + timedelta(days=section_day - 1)

            task = Task(
                week_id=week.id,
                title=tmpl_task.title,
                goal=tmpl_task.title,
                topic=getattr(tmpl_task, "description", None),
                notes="",
                sort_order=sort_idx,
                status=StatusEnum.NOT_STARTED.value,
            )
            if hasattr(Task, "due_date"):
                task.due_date = due_date
            db.session.add(task)

    db.session.commit()
    return plan, all_weeks
