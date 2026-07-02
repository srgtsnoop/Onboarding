# app.py
from __future__ import annotations
from sqlalchemy.orm import selectinload

import os, re
from pathlib import Path
from datetime import date, datetime, timedelta
from dateutil import parser as dtparser

import time

import bleach
from markdown_it import MarkdownIt
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    abort,
    session,
    flash,
)
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
    filter_weeks_for_principal,
    get_current_principal,
)


# 2) Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.join(BASE_DIR, "Onboarding")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(PROJ_DIR, "static")
INSTANCE_DIR = os.path.join(PROJ_DIR, "instance")

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

    db.create_all()

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
                notes="",
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
# Markdown and HTML sanitization
# -----------------------------------------------------------------------------
md = MarkdownIt("commonmark", {"breaks": True, "html": True, "linkify": True})


def render_markdown(text: str | None) -> str:
    if not text:
        return ""

    # Convert markdown to HTML
    html = md.render(text.strip())

    # Sanitize the HTML to allow only safe tags, including links
    allowed_tags = [
        "a",
        "p",
        "br",
        "strong",
        "em",
        "ul",
        "ol",
        "li",
        "code",
        "pre",
        "u",
    ]
    allowed_attrs = {"a": ["href", "title", "target"]}

    clean_html = bleach.clean(
        html, tags=allowed_tags, attributes=allowed_attrs, strip=True
    )
    return clean_html


# -----------------------------------------------------------------------------
# Context processors
# -----------------------------------------------------------------------------


@app.context_processor
def inject_markdown_renderer():
    return dict(render_markdown=render_markdown)


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


def _has_header_principal() -> bool:
    return "X-User-Role" in request.headers or "X-User-Id" in request.headers


def week_progress(week: Week) -> dict:
    """Completion stats for a week: total, done, percent, overdue count."""
    tasks = week.tasks or []
    total = len(tasks)
    done = sum(1 for t in tasks if t.is_complete())
    today = date.today()
    overdue = sum(
        1 for t in tasks if t.due_date and t.due_date < today and not t.is_complete()
    )
    percent = int(round(done / total * 100)) if total else 0
    return {"total": total, "done": done, "percent": percent, "overdue": overdue}


@app.get("/weeks")
def weeks():
    user = optional_current_user()

    if _has_header_principal():
        # API/header-driven access: honor the role-based access policy.
        principal = get_current_principal()
        plan_weeks = (
            filter_weeks_for_principal(principal)
            .options(selectinload(Week.tasks))
            .order_by(Week.start_date.asc().nullsfirst(), Week.id.asc())
            .all()
        )
    else:
        if user is None:
            abort(404, description="No users available")
        plan_weeks = weeks_for_plan(user.onboarding_plan_id)

    progress = {w.id: week_progress(w) for w in plan_weeks}
    totals = {
        "total": sum(p["total"] for p in progress.values()),
        "done": sum(p["done"] for p in progress.values()),
        "overdue": sum(p["overdue"] for p in progress.values()),
    }
    totals["percent"] = (
        int(round(totals["done"] / totals["total"] * 100)) if totals["total"] else 0
    )

    return render_template(
        "weeks.html",
        weeks=plan_weeks,
        user=user or {"role": "guest"},
        progress=progress,
        totals=totals,
    )


@app.get("/weeks/<int:week_id>")
def week_detail(week_id: int):
    user = optional_current_user()

    if _has_header_principal():
        w = Week.query.options(selectinload(Week.tasks)).get(week_id)
        if not w:
            abort(404)
        ensure_week_access(get_current_principal(), w)
    else:
        if user is None:
            abort(404, description="No users available")
        w = ensure_week_for_user(week_id, user)

    tasks = (
        Task.query.filter_by(week_id=w.id)
        .order_by(Task.sort_order.asc(), Task.id.asc())
        .all()
    )
    return render_template(
        "week_detail.html",
        w=w,
        tasks=tasks,
        user=user or {"role": "guest"},
        progress=week_progress(w),
    )


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


@app.route("/admin/users/<int:user_id>/plan/delete", methods=["GET", "POST"])
def admin_delete_user_plan(user_id: int):
    admin = current_user()
    require_role(admin, {RoleEnum.ADMIN.value})

    target_user = User.query.get_or_404(user_id)
    plan = target_user.onboarding_plan

    if not plan:
        # No plan to delete, redirect back to overview
        return redirect(url_for("admin_overview"))

    # Check for started tasks (In Progress or Complete)
    started_tasks_count = (
        Task.query.join(Week)
        .filter(
            Week.onboarding_plan_id == plan.id,
            Task.status.in_([StatusEnum.IN_PROGRESS.value, StatusEnum.COMPLETE.value]),
        )
        .count()
    )

    # If this is an HTMX request (from the modal trigger), return the modal partial
    if request.headers.get("HX-Request") and request.method == "GET":
        return render_template(
            "admin_delete_plan_modal.html",
            admin=admin,
            target_user=target_user,
            plan=plan,
            started_tasks_count=started_tasks_count,
        )

    if request.method == "POST":
        # If started tasks exist, verify confirmation
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

        # Proceed with deletion
        target_user.onboarding_plan_id = None
        db.session.delete(plan)
        db.session.commit()
        flash(
            f"Onboarding plan for {target_user.full_name} has been removed.", "success"
        )
        return redirect(url_for("admin_overview"))

    return render_template(
        "admin_delete_plan_confirm.html",
        admin=admin,
        target_user=target_user,
        plan=plan,
        started_tasks_count=started_tasks_count,
    )


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


@app.get("/templates/import")
def import_template_form():
    user = current_user()
    require_builder_or_admin(user)
    return render_template("template_import.html", user=user)


@app.post("/templates/import")
def import_template_process():
    user = current_user()
    require_builder_or_admin(user)

    if "file" not in request.files:
        return redirect(request.url)

    f = request.files["file"]
    if not f.filename:
        return redirect(request.url)

    if not (
        f.filename.lower().endswith(".docx") or f.filename.lower().endswith(".dotx")
    ):
        return (
            render_template(
                "template_import.html",
                user=user,
                error="Invalid file type. Please upload a .docx file.",
            ),
            400,
        )

    try:
        import docx
    except ImportError:
        return (
            render_template(
                "template_import.html",
                user=user,
                error="Server missing 'python-docx' library. Please install it.",
            ),
            500,
        )

    try:
        # 1. Create the Template container
        base_name = os.path.splitext(os.path.basename(f.filename))[0]
        tpl = OnboardingTemplate(
            name=f"Imported: {base_name}",
            description=f"Imported from {f.filename} on {date.today()}",
            status=TemplateStatusEnum.DRAFT.value,
            created_by_id=user.id,
        )
        db.session.add(tpl)
        db.session.flush()

        # 2. Parse the DOCX
        doc = docx.Document(f)
        week_pattern = re.compile(r"Week\s+(\d+)", re.IGNORECASE)
        section_order = 1

        for table in doc.tables:
            if not table.rows:
                continue

            # 1. Identify Header Row (Training / Outcomes)
            # Check first 3 rows to find headers
            header_row_index = -1
            train_idx = -1
            outcome_idx = -1

            for r_idx in range(min(3, len(table.rows))):
                row_cells = [c.text.strip().lower() for c in table.rows[r_idx].cells]
                t_i = -1
                o_i = -1
                for c_i, txt in enumerate(row_cells):
                    if "training" in txt:
                        t_i = c_i
                    if "outcome" in txt or "goal" in txt:
                        o_i = c_i

                if t_i != -1 and o_i != -1:
                    header_row_index = r_idx
                    train_idx = t_i
                    outcome_idx = o_i
                    break

            if header_row_index == -1:
                continue

            # 2. Determine Section Title & Offset
            title = f"Section {section_order}"
            offset_days = (section_order - 1) * 7

            # If header is not row 0, check row 0 for title info
            if header_row_index > 0:
                # Combine text from row 0 cells to find "Week X"
                row0_text_parts = []
                seen_text = set()
                for c in table.rows[0].cells:
                    t = c.text.strip()
                    if t and t not in seen_text:
                        row0_text_parts.append(t)
                        seen_text.add(t)

                row0_text = " ".join(row0_text_parts).strip()
                if row0_text:
                    title = row0_text
                    match = week_pattern.search(row0_text)
                    if match:
                        try:
                            w_num = int(match.group(1))
                            offset_days = max(0, (w_num - 1) * 7)
                        except ValueError:
                            pass

            # Create Section
            section = TemplateSection(
                template_id=tpl.id,
                title=title,
                offset_days=offset_days,
                order_index=section_order,
            )
            db.session.add(section)
            db.session.flush()
            section_order += 1

            # --- Parse Tasks (Rows after header) ---
            task_order = 1
            for row in table.rows[header_row_index + 1 :]:
                cells = row.cells
                if len(cells) <= max(train_idx, outcome_idx):
                    continue

                t_title = cells[train_idx].text.strip()
                t_desc = cells[outcome_idx].text.strip()

                if not t_title:
                    continue

                task = TemplateTask(
                    section_id=section.id,
                    title=t_title,
                    description=t_desc,
                    responsible_party=ResponsiblePartyEnum.NEW_HIRE.value,
                    due_type=DueTypeEnum.DAY_WITHIN_SECTION.value,
                    section_day=1,
                    order_index=task_order,
                    is_required=False,
                )
                db.session.add(task)
                task_order += 1

        db.session.commit()
        return redirect(url_for("preview_template", template_id=tpl.id))

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Import failed: {e}")
        return (
            render_template(
                "template_import.html",
                user=user,
                error=f"Error processing file: {str(e)}",
            ),
            500,
        )


@app.route("/templates/<int:template_id>/preview", methods=["GET", "POST"])
def preview_template(template_id: int):
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

    if request.method == "POST":
        # Update Template Name
        new_name = request.form.get("name", "").strip()
        if new_name:
            tpl.name = new_name

        # Update Section Titles
        for section in tpl.sections:
            key = f"section_{section.id}_title"
            new_sec_title = request.form.get(key, "").strip()
            if new_sec_title:
                section.title = new_sec_title

        db.session.commit()
        return redirect(url_for("edit_template", template_id=tpl.id))

    return render_template("template_import_preview.html", user=user, template=tpl)


@app.post("/templates/<int:template_id>/cancel_import")
def cancel_import(template_id: int):
    user = current_user()
    require_builder_or_admin(user)

    tpl = OnboardingTemplate.query.get_or_404(template_id)
    db.session.delete(tpl)
    db.session.commit()

    return redirect(url_for("templates_dashboard"))


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


@app.post("/templates/<int:template_id>/sections/<int:section_id>/delete")
def delete_template_section(template_id: int, section_id: int):
    user = current_user()
    require_builder_or_admin(user)

    section = TemplateSection.query.filter_by(
        id=section_id, template_id=template_id
    ).first_or_404()

    for task in section.tasks:
        db.session.delete(task)

    db.session.delete(section)
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


@app.post(
    "/templates/<int:template_id>/sections/<int:section_id>/tasks/<int:task_id>/delete"
)
def delete_template_task(template_id: int, section_id: int, task_id: int):
    user = current_user()
    require_builder_or_admin(user)

    section = TemplateSection.query.filter_by(
        id=section_id, template_id=template_id
    ).first_or_404()

    task = TemplateTask.query.filter_by(
        id=task_id, section_id=section.id
    ).first_or_404()

    db.session.delete(task)
    db.session.commit()

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


@app.post("/templates/<int:template_id>/delete")
def delete_template(template_id: int):
    user = current_user()
    require_builder_or_admin(user)

    tpl = OnboardingTemplate.query.get_or_404(template_id)

    db.session.delete(tpl)
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
    user = optional_current_user()
    if not user or user.role != RoleEnum.ADMIN.value:
        abort(403)
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
