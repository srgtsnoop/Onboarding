# app.py
from __future__ import annotations
from sqlalchemy.orm import selectinload

import os, re
from pathlib import Path
from datetime import date, datetime, timedelta
from dateutil import parser as dtparser

import time

from flask import Flask, render_template, request, redirect, url_for, abort
from Onboarding.models import Week, Task, StatusEnum, User, RoleEnum, OnboardingPlan

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


def weeks_for_plan(plan_id: int | None):
    if plan_id is None:
        return []
    return (
        Week.query.filter_by(onboarding_plan_id=plan_id)
        .options(selectinload(Week.tasks))
        .order_by(Week.start_date.asc().nullsfirst(), Week.id.asc())
        .all()
    )


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
def inject_build_ts():
    return {"build_ts": int(time.time())}


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
    return render_template("_task_notes_form.html", t=t)


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
        return render_template("_task_notes_display.html", t=t)
    return redirect(url_for("week_detail", week_id=t.week_id, as_user=user.email))


@app.get("/tasks/<int:task_id>/notes/cancel", endpoint="cancel_notes_edit")
def cancel_notes_edit(task_id):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    return render_template("_task_notes_display.html", t=t)


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
    return render_template("_task_date_form.html", t=t)


@app.get("/tasks/<int:task_id>/due-date/display", endpoint="date_display")
def date_display(task_id: int):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    return render_template("_task_date_display.html", t=t)


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
    return render_template("_task_date_display.html", t=t)


# -----------------------------------------------------------------------------
# Routes: STATUS inline edit using click → dropdown → auto-save (HTMX)
# -----------------------------------------------------------------------------
@app.get("/tasks/<int:task_id>/status/edit", endpoint="edit_status_form")
def edit_status_form(task_id: int):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    return render_template("_task_status_form.html", t=t)


@app.get("/tasks/<int:task_id>/status/view", endpoint="view_status")
def view_status(task_id: int):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    return render_template("_task_status_display.html", t=t)


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
        return render_template("_task_status_display.html", t=t)

    # Non-HTMX fallback: go back to the week page
    return redirect(url_for("week_detail", week_id=t.week_id, as_user=user.email))


@app.get("/tasks/<int:task_id>/status/cancel")
def cancel_status_edit(task_id):
    user = current_user()
    t = ensure_task_for_user(task_id, user)
    # Just go back to the normal display view for this cell
    return render_template("_task_status_display.html", t=t)


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
