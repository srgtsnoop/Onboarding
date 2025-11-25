# app.py
from __future__ import annotations
from sqlalchemy.orm import selectinload

import os, re
from pathlib import Path
from datetime import date, datetime, timedelta
from dateutil import parser as dtparser

import time

from flask import Flask, render_template, request, redirect, url_for, abort
from Onboarding.models import Week, Task, StatusEnum

# 1) Single shared db instance
from Onboarding.extensions import db
from Onboarding.policy import (
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


@app.context_processor
def inject_build_ts():
    return {"build_ts": int(time.time())}


# -----------------------------------------------------------------------------
# Routes: basic pages
# -----------------------------------------------------------------------------
@app.get("/")
def index():
    return redirect(url_for("weeks"))


@app.get("/weeks")
def weeks():
    principal = get_current_principal()
    all_weeks = (
        filter_weeks_for_principal(principal).order_by(Week.start_date.asc()).all()
    )
    return render_template("weeks.html", weeks=all_weeks)


@app.get("/weeks/<int:week_id>")
def week_detail(week_id: int):
    principal = get_current_principal()
    w = Week.query.get_or_404(week_id)
    ensure_week_access(principal, w)
    tasks = (
        Task.query.filter_by(week_id=w.id)
        .order_by(Task.sort_order.asc(), Task.id.asc())
        .all()
    )
    return render_template("week_detail.html", w=w, tasks=tasks)


# -----------------------------------------------------------------------------
# Routes: create task
# -----------------------------------------------------------------------------
@app.post("/weeks/<int:week_id>/tasks")
def add_task(week_id: int):
    principal = get_current_principal()
    w = Week.query.get_or_404(week_id)
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
    return redirect(url_for("week_detail", week_id=w.id))


# -----------------------------------------------------------------------------
# Routes: edit NOTES (inline)
# Returns the refreshed row partial by default
# -----------------------------------------------------------------------------
@app.get("/tasks/<int:task_id>/notes/edit", endpoint="edit_notes_form")
def edit_notes_form(task_id):
    principal = get_current_principal()
    t = Task.query.get_or_404(task_id)
    ensure_week_access(principal, t.week)
    return render_template("_task_notes_form.html", t=t)


@app.post("/tasks/<int:task_id>/notes", endpoint="update_notes")
def update_notes(task_id):
    principal = get_current_principal()
    t = Task.query.get_or_404(task_id)
    ensure_week_access(principal, t.week)
    raw_notes = request.form.get("notes") or ""
    t.notes = raw_notes.strip()  # 👈 trims leading/trailing whitespace
    db.session.commit()

    if request.headers.get("HX-Request"):
        return render_template("_task_notes_display.html", t=t)
    return redirect(url_for("week_detail", week_id=t.week_id))


@app.get("/tasks/<int:task_id>/notes/cancel", endpoint="cancel_notes_edit")
def cancel_notes_edit(task_id):
    principal = get_current_principal()
    t = Task.query.get_or_404(task_id)
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
    principal = get_current_principal()
    t = Task.query.get_or_404(task_id)
    ensure_week_access(principal, t.week)
    return render_template("_task_date_form.html", t=t)


@app.get("/tasks/<int:task_id>/due-date/display", endpoint="date_display")
def date_display(task_id: int):
    principal = get_current_principal()
    t = Task.query.get_or_404(task_id)
    ensure_week_access(principal, t.week)
    return render_template("_task_date_display.html", t=t)


@app.post("/tasks/<int:task_id>/due-date", endpoint="update_due_date")
def update_due_date(task_id: int):
    principal = get_current_principal()
    t = Task.query.get_or_404(task_id)
    ensure_week_access(principal, t.week)

    # Clear?
    if request.form.get("clear") == "1":
        t.due_date = None
        db.session.commit()
        return render_template("_task_date_display.html", t=t)

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
    principal = get_current_principal()
    t = Task.query.get_or_404(task_id)
    ensure_week_access(principal, t.week)
    return render_template("_task_status_form.html", t=t)


@app.get("/tasks/<int:task_id>/status/view", endpoint="view_status")
def view_status(task_id: int):
    principal = get_current_principal()
    t = Task.query.get_or_404(task_id)
    ensure_week_access(principal, t.week)
    return render_template("_task_status_display.html", t=t)


@app.post("/tasks/<int:task_id>/status", endpoint="update_status")
def update_status(task_id: int):
    principal = get_current_principal()
    t = Task.query.get_or_404(task_id)
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
        return redirect(url_for("week_detail", week_id=t.week_id))

    # Convert string to enum by value and save
    t.status = StatusEnum(label)
    db.session.commit()

    # If this was an HTMX request, return the display fragment
    if request.headers.get("HX-Request"):
        return render_template("_task_status_display.html", t=t)

    # Non-HTMX fallback: go back to the week page
    return redirect(url_for("week_detail", week_id=t.week_id))


@app.get("/tasks/<int:task_id>/status/cancel")
def cancel_status_edit(task_id):
    t = Task.query.get_or_404(task_id)
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
