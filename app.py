# app.py
from __future__ import annotations
from sqlalchemy.orm import selectinload

import os, re
from pathlib import Path
from datetime import date, datetime, timedelta
from dateutil import parser as dtparser

import time

from flask import Flask, render_template, request, redirect, url_for, abort, session
from Onboarding.models import (
    Week,
    Task,
    StatusEnum,
    User,
    RoleEnum,
    OnboardingPlan,
    OnboardingTemplate,
    TemplateSection,
    TemplateTask,
    TemplateStatusEnum,
    ResponsiblePartyEnum,
    DueTypeEnum,
)

# 1) Single shared db instance
from Onboarding.extensions import db
from Onboarding.policy import (
    Principal,
    ensure_week_access,
    get_current_principal,
)


# 2) Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.join(BASE_DIR, "Onboarding")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(PROJ_DIR, "static")
INSTANCE_DIR = os.path.join(PROJ_DIR, "instance")

print("BASE_DIR     =", BASE_DIR)
print("PROJ_DIR     =", PROJ_DIR)
print("TEMPLATES_DIR=", TEMPLATES_DIR)
print("STATIC_DIR   =", STATIC_DIR)
print("INSTANCE_DIR =", INSTANCE_DIR)

os.makedirs(INSTANCE_DIR, exist_ok=True)

# 3) Create app
app = Flask(
    __name__,
    template_folder=TEMPLATES_DIR,
    static_folder=STATIC_DIR,
    instance_path=INSTANCE_DIR,
)

# 4) DB config (forward slashes)
DB_PATH = Path(BASE_DIR, "db.sqlite3").as_posix()
app.config.update(
    SQLALCHEMY_DATABASE_URI=f"sqlite:///{DB_PATH}",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret"),
)

# 5) Bind db to app
db.init_app(app)

# 6) ***CRITICAL***: Import models NOW (register tables on this db metadata)
import Onboarding.models as _models  # noqa: F401

# Optional: bring names into this module if you want
from Onboarding.models import Week, Task, StatusEnum  # noqa: E402

# 7) Create tables and prove they exist
with app.app_context():
    from sqlalchemy import inspect

    print("Metadata tables pre-create_all:", list(db.metadata.tables))
    db.create_all()
    print("Tables now:", inspect(db.engine).get_table_names())

# -----------------------------------------------------------------------------
# Helpers: parse user-entered date strings for due_date
# Supports: YYYY-MM-DD, today, tomorrow, +N, -N, and lenient dateutil parsing
# -----------------------------------------------------------------------------
RELATIVE_RE = re.compile(r"^([+-])\s*(\d{1,3})$")


def parse_due_date(text: str | None) -> date | None:
    if text is None:
        return None
    s = text.strip()
    if not s:
        return None

    today = date.today()

    lower = s.lower()
    if lower == "today":
        return today
    if lower == "tomorrow":
        return today + timedelta(days=1)

    m = RELATIVE_RE.match(s)
    if m:
        sign, num = m.groups()
        delta = int(num)
        return today + (
            timedelta(days=delta) if sign == "+" else timedelta(days=-delta)
        )

    try:
        dt = dtparser.parse(s, dayfirst=False, yearfirst=True)
        return dt.date()
    except Exception as exc:
        raise ValueError(f"Could not parse due date: {s}") from exc


# -----------------------------------------------------------------------------
# Helpers: user resolution and authorization
# -----------------------------------------------------------------------------
def current_user() -> User:
    """Resolve the acting user from a header or query param.

    Falls back to the first user in the database so the UI continues to work in
    development without explicit context. Raises 404 if no users exist.
    """

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
    """Prefer an explicit header principal, otherwise derive from ``user``."""

    if "X-User-Role" in request.headers or "X-User-Id" in request.headers:
        return get_current_principal()

    return Principal(user_id=user.id, role=user.role)


def require_role(user: User, allowed_roles: set[str]):
    if user.role not in allowed_roles:
        abort(403)


def require_builder_or_admin(user: User):
    """
    Allow only Builder or Admin roles to access certain routes (e.g., templates).
    """
    allowed = {RoleEnum.BUILDER.value, RoleEnum.ADMIN.value}
    if user.role not in allowed:
        abort(403)


def require_manager_or_admin(user: User):
    """
    Allow Manager and Admin to assign onboarding plans.
    Optionally also allow Builder if that role exists.
    """
    allowed = {RoleEnum.MANAGER.value, RoleEnum.ADMIN.value}
    # If your RoleEnum has BUILDER, let them in too:
    if hasattr(RoleEnum, "BUILDER"):
        allowed.add(RoleEnum.BUILDER.value)
    require_role(user, allowed)


def weeks_for_plan(plan_id: int | None):
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
) -> OnboardingPlan:
    """
    Given a template id, a user, and a start date, create an OnboardingPlan,
    Weeks, and Tasks based on the template structure.
    """

    tpl = (
        OnboardingTemplate.query.options(
            selectinload(OnboardingTemplate.sections).selectinload(
                TemplateSection.tasks
            )
        )
        .filter_by(id=template_id)
        .first_or_404()
    )

    # Create the plan
    plan = OnboardingPlan(
        name=tpl.name,
        description=tpl.description,
    )

    # If your model has these fields, set them:
    if hasattr(OnboardingPlan, "template_id"):
        plan.template_id = tpl.id
    if hasattr(OnboardingPlan, "start_date"):
        plan.start_date = start_date

    db.session.add(plan)
    db.session.flush()  # get plan.id without full commit yet

    # Attach the plan to the employee
    employee.onboarding_plan_id = plan.id

    # Build weeks for each section
    # Sort sections by order_index if you have it, then id for stability
    sections = sorted(
        tpl.sections,
        key=lambda s: ((getattr(s, "order_index", None) or 0), s.id),
    )

    all_weeks = []

    for section in sections:
        # Calculate section/“week” start from base start_date + offset_days
        offset_days = getattr(section, "offset_days", None) or 0
        week_start = start_date + timedelta(days=offset_days)
        week_end = week_start + timedelta(days=6)

        week = Week(
            onboarding_plan_id=plan.id,
            title=section.title,
            start_date=week_start,
            end_date=week_end,
            owner_user_id=employee.id,
            manager_user_id=employee.manager_id,
        )
        db.session.add(week)
        db.session.flush()  # so week.id exists for tasks
        all_weeks.append(week)

        # Tasks: sorted by order_index then id
        section_tasks = sorted(
            section.tasks,
            key=lambda t: ((getattr(t, "order_index", None) or 0), t.id),
        )

        for sort_idx, tmpl_task in enumerate(section_tasks, start=1):
            # Compute due date from template rules
            due_date = None
            due_type = getattr(tmpl_task, "due_type", None)
            t_offset = getattr(tmpl_task, "offset_days", None)
            section_day = getattr(tmpl_task, "section_day", None)

            if due_type == "days_from_start" and t_offset is not None:
                due_date = start_date + timedelta(days=t_offset)
            elif due_type == "day_within_section" and section_day is not None:
                due_date = week_start + timedelta(days=section_day - 1)

            # Build Task from template task
            task = Task(
                week_id=week.id,
                title=tmpl_task.title,
                goal=tmpl_task.title,
                topic=getattr(tmpl_task, "description", None),
                notes=getattr(tmpl_task, "description", None),
                sort_order=sort_idx,
                status=StatusEnum.NOT_STARTED.value,
            )

            # Only set due_date if your Task model actually has it
            if hasattr(Task, "due_date"):
                task.due_date = due_date

            # If you later add an assigned_to or responsible_role on Task,
            # you can copy tmpl_task.responsible_party here.

            db.session.add(task)

    db.session.commit()
    return plan, all_weeks


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



# -----------------------------------------------------------------------------
# Context processors
# -----------------------------------------------------------------------------


@app.context_processor
def inject_user():
    u = session.get("user")
    # normalize to an object-ish mapping for Jinja
    return {"user": u or {"role": "guest"}}


@app.context_processor
def inject_user_switcher():
    users = User.query.order_by(User.role.asc(), User.full_name.asc()).all()
    active_user = None
    try:
        active_user = current_user()
    except Exception:
        active_user = None

    return {
        "switchable_users": users,
        "nav_current_user": active_user,
    }


# -----------------------------------------------------------------------------
# Routes: basic pages
# -----------------------------------------------------------------------------
@app.get("/")
def index():
    return redirect(url_for("weeks"))


@app.get("/weeks")
def weeks():
    user = current_user()
    plan_weeks = weeks_for_plan(user.onboarding_plan_id)
    return render_template("weeks.html", weeks=plan_weeks, user=user)


@app.get("/weeks/<int:week_id>")
def week_detail(week_id: int):
    user = current_user()
    w = ensure_week_for_user(week_id, user)
    tasks = (
        Task.query.filter_by(week_id=w.id)
        .order_by(Task.sort_order.asc(), Task.id.asc())
        .all()
    )
    return render_template("week_detail.html", w=w, tasks=tasks, user=user)


@app.get("/templates")
def templates_dashboard():
    user = current_user()
    require_builder_or_admin(user)

    templates = OnboardingTemplate.query.order_by(
        OnboardingTemplate.status.asc(),
        OnboardingTemplate.name.asc(),
    ).all()

    return render_template(
        "templates_dashboard.html",
        user=user,
        templates=templates,
    )


# -----------------------------------------------------------------------------
# Role-aware endpoints
# -----------------------------------------------------------------------------
@app.get("/api/my-plan")
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


@app.get("/manager/reports")
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

    return render_template(
        "manager_reports.html",
        manager=manager,
        reports=reports,
    )


@app.get("/api/manager/reports")
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
        "manager": {
            "id": manager.id,
            "email": manager.email,
            "full_name": manager.full_name,
        },
        "direct_reports": [serialize_user_with_plan(u) for u in reports],
    }


@app.get("/admin/overview")
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
        users=users,
        plans=plans,
    )


@app.get("/api/admin/overview")
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


# -----------------------------------------------------------------------------
# Routes: create task
# -----------------------------------------------------------------------------
@app.post("/weeks/<int:week_id>/tasks")
def add_task(week_id: int):
    user = current_user()
    principal = resolve_principal(user)
    w = ensure_week_for_user(week_id, user)
    ensure_week_access(principal, w)
    title = request.form.get("goal", "").strip()
    topic = request.form.get("topic", "").strip()
    notes = request.form.get("notes", "").strip()
    due_raw = request.form.get("due_date", "").strip()

    # parse date if provided and model supports it
    due_date = None
    if due_raw:
        try:
            due_date = datetime.strptime(due_raw, "%Y-%m-%d").date()
        except ValueError:
            pass  # keep None

    t = Task(
        week_id=w.id,
        title=title,
        topic=topic,
        notes=notes,
        # status="Not Started",  # default status
        sort_order=(Task.query.filter_by(week_id=w.id).count() + 1),
    )
    # only set due_date if your model has that column
    if hasattr(Task, "due_date"):
        t.due_date = due_date

    db.session.add(t)
    db.session.commit()
    return redirect(url_for("week_detail", week_id=w.id, as_user=user.email))


# -----------------------------------------------------------------------------
# Routes: edit NOTES (inline)
# Returns the refreshed row partial by default
# -----------------------------------------------------------------------------
@app.get("/tasks/<int:task_id>/notes/edit", endpoint="edit_notes_form")
def edit_notes_form(task_id):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    return render_template("_task_notes_form.html", t=t, user=user)


@app.post("/tasks/<int:task_id>/notes", endpoint="update_notes")
def update_notes(task_id):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    raw_notes = request.form.get("notes") or ""
    t.notes = raw_notes.strip()  # 👈 trims leading/trailing whitespace
    db.session.commit()

    if request.headers.get("HX-Request"):
        return render_template("_task_notes_display.html", t=t, user=user)
    return redirect(url_for("week_detail", week_id=t.week_id, as_user=user.email))


@app.get("/tasks/<int:task_id>/notes/cancel", endpoint="cancel_notes_edit")
def cancel_notes_edit(task_id):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    return render_template("_task_notes_display.html", t=t, user=user)


# -----------------------------------------------------------------------------
# Routes: edit DUE DATE (inline)
# Accepts user-friendly strings (today, +3, 2025-10-08, etc.)
# -----------------------------------------------------------------------------
def _parse_due_date(s: str):
    """Accept YYYY-MM-DD (browser date), MM/DD/YY, MM/DD/YYYY."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ---------- Due date inline edit (HTMX) ----------
@app.get("/tasks/<int:task_id>/due-date/form", endpoint="edit_date_form")
def edit_date_form(task_id: int):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    return render_template("_task_date_form.html", t=t, user=user)


@app.get("/tasks/<int:task_id>/due-date/display", endpoint="date_display")
def date_display(task_id: int):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    return render_template("_task_date_display.html", t=t, user=user)


@app.post("/tasks/<int:task_id>/due-date", endpoint="update_due_date")
def update_due_date(task_id: int):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)

    # Clear?
    if request.form.get("clear") == "1":
        t.due_date = None
        db.session.commit()
        if request.headers.get("HX-Request"):
            return render_template("_task_date_display.html", t=t)
        return redirect(url_for("week_detail", week_id=t.week_id, as_user=user.email))

    # Try both inputs (native date + freeform text)
    raw = (
        request.form.get("due_date") or request.form.get("due_date_text") or ""
    ).strip()
    parsed = _parse_due_date(raw)

    if raw and parsed is None:
        # Re-render the form with an error
        return (
            render_template(
                "_task_date_form.html", t=t, error="Enter a valid date (MM/DD/YY)."
            ),
            400,
        )

    t.due_date = parsed
    db.session.commit()
    return render_template("_task_date_display.html", t=t, user=user)


# -----------------------------------------------------------------------------
# Routes: STATUS inline edit using click → dropdown → auto-save (HTMX)
# -----------------------------------------------------------------------------
@app.get("/tasks/<int:task_id>/status/edit", endpoint="edit_status_form")
def edit_status_form(task_id: int):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    return render_template("_task_status_form.html", t=t, user=user)


@app.get("/tasks/<int:task_id>/status/view", endpoint="view_status")
def view_status(task_id: int):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    return render_template("_task_status_display.html", t=t, user=user)


@app.post("/tasks/<int:task_id>/status", endpoint="update_status")
def update_status(task_id: int):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)

    # Get the label from the form and normalize it
    label = (request.form.get("status") or "").strip()

    # Validate against Enum values
    valid_values = [e.value for e in StatusEnum]
    if label not in valid_values:
        error = "Invalid status"
        if request.headers.get("HX-Request"):
            # Re-render the form with an error for HTMX requests
            return (
                render_template("_task_status_form.html", t=t, error=error),
                400,
            )
        # Non-HTMX fallback – just redirect (optional: flash a message)
        return redirect(url_for("week_detail", week_id=t.week_id, as_user=user.email))

    # Convert string to enum by value and save
    t.status = StatusEnum(label)
    db.session.commit()

    # If this was an HTMX request, return the display fragment
    if request.headers.get("HX-Request"):
        return render_template("_task_status_display.html", t=t, user=user)

    # Non-HTMX fallback: go back to the week page
    return redirect(url_for("week_detail", week_id=t.week_id, as_user=user.email))


@app.get("/tasks/<int:task_id>/status/cancel")
def cancel_status_edit(task_id):
    user = current_user()
    t = ensure_task_for_user(task_id, user)
    # Just go back to the normal display view for this cell
    return render_template("_task_status_display.html", t=t, user=user)


# -----------------------------------------------------------------------------
# Routes: Create simple onboarding templates
# -----------------------------------------------------------------------------


@app.get("/templates/new")
def new_template_form():
    user = current_user()
    require_builder_or_admin(user)
    return render_template("template_new.html", user=user)


@app.post("/templates")
def create_template():
    user = current_user()
    require_builder_or_admin(user)

    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()

    if not name:
        # Re-render form with a simple error
        return (
            render_template(
                "template_new.html",
                user=user,
                error="Template name is required.",
                form={"name": name, "description": description},
            ),
            400,
        )

    tpl = OnboardingTemplate(
        name=name,
        description=description,
        status=TemplateStatusEnum.DRAFT.value,
        created_by_id=user.id,
    )
    db.session.add(tpl)
    db.session.commit()

    # Redirect to future template editor page
    return redirect(url_for("edit_template", template_id=tpl.id))


@app.get("/templates/<int:template_id>/edit")
def edit_template(template_id: int):
    user = current_user()
    require_builder_or_admin(user)

    tpl = (
        OnboardingTemplate.query.options(
            selectinload(OnboardingTemplate.sections).selectinload(
                TemplateSection.tasks
            )
        )
        .filter_by(id=template_id)
        .first_or_404()
    )

    return render_template(
        "template_edit.html",
        user=user,
        template=tpl,
    )


# -----------------------------------------------------------------------------
#  Routes: Edit template sections and tasks (AJAX/HTMX)
# -----------------------------------------------------------------------------


@app.post("/templates/<int:template_id>/sections")
def add_template_section(template_id: int):
    user = current_user()
    require_builder_or_admin(user)

    tpl = OnboardingTemplate.query.get_or_404(template_id)

    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    offset_raw = (request.form.get("offset_days") or "").strip()

    if not title:
        # no title: just go back for now; you can add proper error messaging later
        return redirect(url_for("edit_template", template_id=template_id))

    try:
        offset_days = int(offset_raw) if offset_raw else None
    except ValueError:
        offset_days = None

    # determine order_index (append to end)
    if tpl.sections:
        max_order = max(s.order_index or 0 for s in tpl.sections)
        order_index = max_order + 1
    else:
        order_index = 1

    section = TemplateSection(
        template_id=tpl.id,
        title=title,
        description=description if description else None,
        offset_days=offset_days,
        order_index=order_index,
    )

    db.session.add(section)
    db.session.commit()

    return redirect(url_for("edit_template", template_id=template_id))


@app.post("/templates/<int:template_id>/sections/<int:section_id>/tasks")
def add_template_task(template_id: int, section_id: int):
    user = current_user()
    require_builder_or_admin(user)

    section = TemplateSection.query.filter_by(
        id=section_id, template_id=template_id
    ).first_or_404()

    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    responsible_raw = (request.form.get("responsible_party") or "new_hire").strip()
    due_type_raw = (request.form.get("due_type") or "days_from_start").strip()
    offset_raw = (request.form.get("offset_days") or "").strip()
    section_day_raw = (request.form.get("section_day") or "").strip()
    is_required_raw = request.form.get("is_required")
    category = (request.form.get("category") or "").strip()

    if not title:
        return redirect(url_for("edit_template", template_id=template_id))

    # map to enums / values
    if responsible_raw not in {"new_hire", "manager", "other"}:
        responsible_raw = "new_hire"

    if due_type_raw not in {e.value for e in DueTypeEnum}:
        due_type_raw = DueTypeEnum.DAYS_FROM_START.value

    try:
        offset_days = (
            int(offset_raw)
            if offset_raw and due_type_raw == DueTypeEnum.DAYS_FROM_START.value
            else None
        )
    except ValueError:
        offset_days = None

    try:
        section_day = (
            int(section_day_raw)
            if section_day_raw and due_type_raw == DueTypeEnum.DAY_WITHIN_SECTION.value
            else None
        )
    except ValueError:
        section_day = None

    is_required = bool(is_required_raw)

    # determine order_index within section
    if section.tasks:
        max_order = max(t.order_index or 0 for t in section.tasks)
        order_index = max_order + 1
    else:
        order_index = 1

    t = TemplateTask(
        section_id=section.id,
        title=title,
        description=description if description else None,
        responsible_party=responsible_raw,
        due_type=due_type_raw,
        offset_days=offset_days,
        section_day=section_day,
        category=category if category else None,
        is_required=is_required,
        order_index=order_index,
    )

    db.session.add(t)
    db.session.commit()

    return redirect(url_for("edit_template", template_id=template_id))


@app.get(
    "/templates/<int:template_id>/sections/<int:section_id>/tasks/<int:task_id>/edit"
)
def edit_template_task(template_id: int, section_id: int, task_id: int):
    user = current_user()
    require_builder_or_admin(user)

    section = TemplateSection.query.filter_by(
        id=section_id, template_id=template_id
    ).first_or_404()

    task = TemplateTask.query.filter_by(
        id=task_id, section_id=section.id
    ).first_or_404()

    tpl = OnboardingTemplate.query.get_or_404(template_id)

    return render_template(
        "template_task_edit.html",
        user=user,
        template=tpl,
        section=section,
        task=task,
    )


@app.post("/templates/<int:template_id>/sections/<int:section_id>/tasks/<int:task_id>")
def update_template_task(template_id: int, section_id: int, task_id: int):
    user = current_user()
    require_builder_or_admin(user)

    section = TemplateSection.query.filter_by(
        id=section_id, template_id=template_id
    ).first_or_404()

    task = TemplateTask.query.filter_by(
        id=task_id, section_id=section.id
    ).first_or_404()

    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()

    # ✅ pull from the same field name as the form
    responsible_raw = (request.form.get("responsible_party") or "new_hire").strip()

    due_type_raw = (request.form.get("due_type") or "days_from_start").strip()
    offset_raw = (request.form.get("offset_days") or "").strip()
    section_day_raw = (request.form.get("section_day") or "").strip()
    is_required_raw = request.form.get("is_required")
    category = (request.form.get("category") or "").strip()

    if not title:
        # no title -> just bounce back to edit page
        return redirect(
            url_for(
                "edit_template_task",
                template_id=template_id,
                section_id=section_id,
                task_id=task_id,
            )
        )

    # ✅ normalize responsible party using simple strings
    if responsible_raw not in {"new_hire", "manager", "other"}:
        responsible_raw = "new_hire"

    # ✅ normalize due type (same idea)
    if due_type_raw not in {"days_from_start", "day_within_section"}:
        due_type_raw = "days_from_start"

    # parse offsets
    offset_days = None
    if due_type_raw == "days_from_start":
        try:
            offset_days = int(offset_raw) if offset_raw else None
        except ValueError:
            offset_days = None

    section_day = None
    if due_type_raw == "day_within_section":
        try:
            section_day = int(section_day_raw) if section_day_raw else None
        except ValueError:
            section_day = None

    is_required = bool(is_required_raw)

    # ✅ apply updates
    task.title = title
    task.description = description or None
    task.responsible_party = responsible_raw
    task.due_type = due_type_raw
    task.offset_days = offset_days
    task.section_day = section_day
    task.category = category or None
    task.is_required = is_required

    db.session.commit()

    # Go back to the template editor
    return redirect(url_for("edit_template", template_id=template_id))


# -----------------------------------------------------------------------------
# Routes: Template Publish
# -----------------------------------------------------------------------------


@app.post("/templates/<int:template_id>/publish")
def publish_template(template_id: int):
    user = current_user()
    require_builder_or_admin(user)

    tpl = OnboardingTemplate.query.get_or_404(template_id)

    # Only allow publishing if it's not retired
    if tpl.status != TemplateStatusEnum.RETIRED.value:
        tpl.status = TemplateStatusEnum.PUBLISHED.value
        db.session.commit()

    return redirect(url_for("templates_dashboard"))


@app.post("/templates/<int:template_id>/retire")
def retire_template(template_id: int):
    user = current_user()
    require_builder_or_admin(user)

    tpl = OnboardingTemplate.query.get_or_404(template_id)

    # Only retire if not already retired
    if tpl.status != TemplateStatusEnum.RETIRED.value:
        tpl.status = TemplateStatusEnum.RETIRED.value
        db.session.commit()

    return redirect(url_for("templates_dashboard"))


# -----------------------------------------------------------------------------
# Routes: Assign a plan to a user
# -----------------------------------------------------------------------------


@app.get("/assign")
def assign_plan_form():
    user = current_user()
    require_manager_or_admin(user)

    # Only published templates should be assignable
    templates = (
        OnboardingTemplate.query.filter_by(status=TemplateStatusEnum.PUBLISHED.value)
        .order_by(OnboardingTemplate.name.asc())
        .all()
    )

    # For now, show all users; you could later filter to role=user or direct reports
    employees = User.query.order_by(User.full_name.asc()).all()

    return render_template(
        "assign_plan.html",
        user=user,
        templates=templates,
        employees=employees,
        today=date.today().isoformat(),
    )


@app.post("/assign")
def assign_plan():
    user = current_user()
    require_manager_or_admin(user)

    template_id_raw = request.form.get("template_id") or ""
    employee_id_raw = request.form.get("user_id") or ""
    start_raw = (request.form.get("start_date") or "").strip()

    # Basic validation
    try:
        template_id = int(template_id_raw)
        employee_id = int(employee_id_raw)
    except ValueError:
        # Invalid selections; just bounce back for now
        return redirect(url_for("assign_plan_form"))

    tpl = OnboardingTemplate.query.get_or_404(template_id)

    # Only allow assignment from published templates
    if tpl.status != TemplateStatusEnum.PUBLISHED.value:
        abort(400, description="Template must be published to assign a plan.")

    employee = User.query.get_or_404(employee_id)

    # Parse start date; default to today if blank or invalid
    start_date = date.today()
    if start_raw:
        try:
            start_date = datetime.strptime(start_raw, "%Y-%m-%d").date()
        except ValueError:
            pass

    plan, weeks = create_plan_from_template(tpl.id, employee, start_date)

    # Choose the first week to land on (earliest start_date if available)
    first_week = None
    if weeks:
        first_week = sorted(
            weeks,
            key=lambda w: (w.start_date or date.max, w.id),
        )[0]

    # Redirect to the new plan view – for now, go to the first week as the new hire
    if first_week:
        return redirect(
            url_for(
                "week_detail",
                week_id=first_week.id,
                as_user=employee.email,
            )
        )

    # If somehow there were no weeks, go to the employee's plan overview or home
    return redirect(url_for("home"))


# -----------------------------------------------------------------------------
# Routes: Manager see assigned templates
# -----------------------------------------------------------------------------


@app.get("/manager/plans")
def manager_plans():
    user = current_user()
    require_manager_or_admin(user)

    # If you have manager_id relationships, you can filter to direct reports.
    # For now: show everyone who has a plan.
    employees = (
        User.query.filter(User.onboarding_plan_id.isnot(None))
        .order_by(User.full_name.asc())
        .all()
    )

    # Build summary stats (complete / total)
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
                Task.week_id.in_(week_ids), Task.status == StatusEnum.COMPLETE.value
            ).count()

        summaries.append(
            {
                "employee": emp,
                "plan": plan,
                "total_tasks": total_tasks,
                "complete_tasks": complete_tasks,
                "progress_pct": (
                    int((complete_tasks / total_tasks) * 100) if total_tasks else 0
                ),
            }
        )

    return render_template("manager_plans.html", user=user, summaries=summaries)


# -----------------------------------------------------------------------------
# Optional: simple health check
# -----------------------------------------------------------------------------
@app.get("/healthz")
def healthz():
    return {"ok": True, "time": datetime.utcnow().isoformat()}


@app.get("/debug/db")
def debug_db():
    return {
        "db_path": app.config["SQLALCHEMY_DATABASE_URI"],
        "weeks_count": Week.query.count(),
        "tasks_count": Task.query.count(),
    }


# -----------------------------------------------------------------------------
# Dev entry
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
