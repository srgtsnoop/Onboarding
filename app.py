# app.py
from __future__ import annotations
from sqlalchemy.orm import selectinload

import os, re
from pathlib import Path
from datetime import date, datetime, timedelta
from dateutil import parser as dtparser

from flask import Flask, render_template, request, redirect, url_for, abort

# 1) Single shared db instance
from Onboarding.extensions import db

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
# Routes: basic pages
# -----------------------------------------------------------------------------
@app.get("/")
def index():
    return redirect(url_for("weeks"))


@app.get("/weeks")
def weeks():
    all_weeks = Week.query.order_by(Week.start_date.asc()).all()
    return render_template("weeks.html", weeks=all_weeks)


@app.get("/weeks/<int:week_id>")
def week_detail(week_id: int):
    w = Week.query.get_or_404(week_id)
    # tasks ordered by sort_order if you have that field; else default by id
    tasks = (
        Task.query.filter_by(week_id=w.id).order_by(Task.sort_order.asc()).all()
        if hasattr(Task, "sort_order")
        else w.tasks
    )
    return render_template("week_detail.html", w=Week, tasks=tasks)


# -----------------------------------------------------------------------------
# Routes: create task
# -----------------------------------------------------------------------------
@app.post("/weeks/<int:week_id>/tasks")
def add_task(week_id: int):
    w = (
        Week.query.options(selectinload(Week.tasks))
        .filter_by(id=week_id)
        .first_or_404()
    )
    goal = (request.form.get("goal") or "").strip()
    topic = (request.form.get("topic") or "").strip() or None
    if not goal:
        abort(400, "Goal is required")

    # sort_order if present
    if hasattr(Task, "sort_order"):
        next_sort = (w.tasks[-1].sort_order + 1) if w.tasks else 0
        t = Task(week_id=w.id, goal=goal, topic=topic, sort_order=next_sort)
    else:
        t = Task(week_id=w.id, goal=goal, topic=topic)

    db.session.add(t)
    db.session.commit()
    return redirect(url_for("week_detail", week_id=w.id))


# -----------------------------------------------------------------------------
# Routes: edit NOTES (inline)
# Returns the refreshed row partial by default
# -----------------------------------------------------------------------------


@app.get("/tasks/<int:task_id>/notes", endpoint="view_notes")
def view_notes(task_id):
    t = Task.query.get_or_404(task_id)
    # return just the display fragment for HTMX
    if request.headers.get("HX-Request"):
        return render_template("_task_row.html", t=t)  # if coming from table
    # non-HTMX fallback: go back to the task’s week page (adjust as needed)
    return redirect(url_for("week_detail", week_id=t.week_id))


@app.get("/tasks/<int:task_id>/notes/edit", endpoint="edit_notes_form")
def edit_notes_form(task_id):
    t = Task.query.get_or_404(task_id)
    return render_template("_task_notes_form.html", t=t)


@app.post("/tasks/<int:task_id>/notes", endpoint="update_notes")
def update_notes(task_id):
    t = Task.query.get_or_404(task_id)
    # pull from form, normalize
    new_notes = (request.form.get("notes") or "").strip()
    t.notes = (request.form.get("notes") or "").strip()
    db.session.commit()
    if request.headers.get("HX-Request"):
        # send the display fragment back into the same target
        return render_template("_task_row.html", t=t)  # or "_task_card.html"
    return redirect(url_for("week_detail", week_id=t.week_id))


# -----------------------------------------------------------------------------
# Routes: edit DUE DATE (inline)
# Accepts user-friendly strings (today, +3, 2025-10-08, etc.)
# -----------------------------------------------------------------------------
# ---------- Due date inline edit (HTMX) ----------
@app.get("/tasks/<int:task_id>/due-date/edit")
def edit_date_form(task_id: int):
    t = Task.query.get_or_404(task_id)
    return render_template("_task_date_form.html", t=t)


@app.get("/tasks/<int:task_id>/due-date/view")
def view_date(task_id: int):
    t = Task.query.get_or_404(task_id)
    return render_template("_task_date_display.html", t=t)


@app.post("/tasks/<int:task_id>/due-date")
def edit_date(task_id: int):
    t = Task.query.get_or_404(task_id)
    raw = (request.form.get("due_date") or "").strip()
    try:
        parsed = parse_due_date(raw)  # your helper
    except ValueError as e:
        return render_template("_task_date_form.html", t=t, error=str(e)), 400
    t.due_date = parsed
    db.session.commit()
    return render_template("_task_date_display.html", t=t)


# -----------------------------------------------------------------------------
# Routes: STATUS inline edit using click → form → Save/Cancel (HTMX)
# -----------------------------------------------------------------------------
@app.get("/tasks/<int:task_id>/status/edit")
def edit_status_form(task_id: int):
    t = Task.query.get_or_404(task_id)
    return render_template("_task_status_form.html", t=t)


@app.get("/tasks/<int:task_id>/status/view")
def view_status(task_id: int):
    t = Task.query.get_or_404(task_id)
    return render_template("_task_status_display.html", t=t)


@app.post("/tasks/<int:task_id>/status")
def edit_status(task_id: int):
    t = Task.query.get_or_404(task_id)
    label = (request.form.get("status") or "").strip()

    # Validate against Enum values
    valid_values = [e.value for e in StatusEnum]
    if label not in valid_values:
        return (
            render_template("_task_status_form.html", t=t, error="Invalid status"),
            400,
        )

    # Convert string to enum by value and save
    t.status = StatusEnum(label)
    db.session.commit()

    # Return to read-only display after save
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
