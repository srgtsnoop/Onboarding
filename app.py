from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta

from dateutil import parser as dtparser
from flask import Flask, render_template, request, redirect, url_for, abort

from models import db, Week, Task, StatusEnum

# ------------------------------
# Date helpers
# ------------------------------

WEEKEND_DAYS = {5, 6}  # 5=Saturday, 6=Sunday


def coerce_date(raw: str) -> date | None:
    """
    Accepts:
      - '' (empty)  -> None
      - 'YYYY-MM-DD' (native <input type=date>)
      - 'MM/DD/YYYY', 'M/D/YY', etc.
      - 'today', 'tomorrow', 'yesterday'
      - '+N' or '-N' (days offset from today)
    Returns a date or raises ValueError.
    """
    if raw is None:
        raise ValueError("No value")

    s = raw.strip().lower()
    if s == "":
        return None

    today = date.today()

    if s in {"today"}:
        return today
    if s in {"tomorrow", "tmrw"}:
        return today + timedelta(days=1)
    if s in {"yesterday"}:
        return today - timedelta(days=1)

    # +N / -N days
    m = re.fullmatch(r"([+-])\s*(\d+)", s)
    if m:
        sign, n = m.groups()
        days = int(n) * (1 if sign == "+" else -1)
        return today + timedelta(days=days)

    # Try strict HTML date first
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        pass

    # Try flexible parsing (10/8/2025, 10-08-25, etc.)
    try:
        # dayfirst=False for US-style dates; change if you prefer.
        return dtparser.parse(s, dayfirst=False).date()
    except Exception as e:
        raise ValueError(f"Unrecognized date: {raw}") from e


def ensure_weekday(d: date | None) -> tuple[date | None, bool]:
    """
    If d is Sat/Sun, roll it forward to the next Monday.
    Returns (new_date_or_none, adjusted_flag).
    """
    if d is None:
        return None, False
    adjusted = False
    while d.weekday() in WEEKEND_DAYS:
        d += timedelta(days=1)
        adjusted = True
    return d, adjusted


# ------------------------------
# App / DB setup
# ------------------------------

app = Flask(__name__)

# Ensure the SQLite file is created in THIS folder (Windows-safe URI)
BASEDIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASEDIR, "db.sqlite3")
DB_URI = "sqlite:///" + DB_PATH.replace("\\", "/")

app.config["SQLALCHEMY_DATABASE_URI"] = DB_URI
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

# ------------------------------
# Routes
# ------------------------------


@app.route("/")
def home():
    return redirect(url_for("weeks"))


@app.route("/weeks")
def weeks():
    weeks = Week.query.order_by(Week.id).all()
    return render_template("weeks.html", weeks=weeks)


@app.route("/week/<int:week_id>")
def week_detail(week_id: int):
    week = Week.query.get_or_404(week_id)
    return render_template("week_detail.html", week=week)


# ---------- Inline update endpoints (HTMX) ----------


@app.post("/task/<int:task_id>/edit-notes")
def edit_notes(task_id: int):
    task = Task.query.get_or_404(task_id)
    return render_template("_task_status_form.html", t=task, StatusEnum=StatusEnum)


@app.post("/task/<int:task_id>/update-notes")
def update_notes(task_id: int):
    task = Task.query.get_or_404(task_id)
    notes = request.form.get("notes", "").strip()
    task.notes = notes or None
    db.session.commit()
    return render_template("_task_row.html", t=task)


@app.post("/task/<int:task_id>/edit-date")
def edit_date(task_id: int):
    task = Task.query.get_or_404(task_id)
    return render_template("_task_date_form.html", t=task)


@app.post("/task/<int:task_id>/update-date")
def update_date(task_id: int):
    task = Task.query.get_or_404(task_id)
    raw = request.form.get("due_date", "")
    try:
        parsed = coerce_date(raw)  # allow flexible inputs (today, 10/8/2025, +2, etc.)
        parsed, adjusted = ensure_weekday(parsed)  # never allow weekend due dates
        task.due_date = parsed  # None clears the date
        db.session.commit()
        note = "Weekend selected—auto-moved to Monday." if adjusted else None
        return render_template("_task_row.html", t=task, adjusted_msg=note)
    except ValueError as err:
        # Re-render the form with an inline error message
        return render_template("_task_date_form.html", t=task, error=str(err)), 422


@app.post("/task/<int:task_id>/edit-status")
def edit_status(task_id):
    task = Task.query.get_or_404(task_id)
    new_status = request.form.get("status")

    valid_statuses = ["Not Started", "In Progress", "Complete"]
    if new_status not in valid_statuses:
        abort(400, f"Invalid status: {new_status}")

    task.status = new_status
    db.session.commit()

    # Return the same partial to update the HTMX region
    return render_template("_task_status_form.html", t=task)


@app.post("/task/<int:task_id>/update-status")
def update_status(task_id: int):
    task = Task.query.get_or_404(task_id)
    new_status = request.form.get("status")
    if new_status not in [s.value for s in StatusEnum]:
        abort(400, "Invalid status")
    task.status = StatusEnum(new_status)
    db.session.commit()
    return render_template("_task_row.html", t=task)


# Create new tasks (simple)
@app.post("/week/<int:week_id>/add-task")
def add_task(week_id: int):
    week = Week.query.get_or_404(week_id)
    goal = request.form.get("goal", "").strip()
    topic = request.form.get("topic", "").strip() or None
    if not goal:
        abort(400, "Goal is required")
    sort_order = (week.tasks[-1].sort_order + 1) if week.tasks else 0
    t = Task(week_id=week.id, goal=goal, topic=topic, sort_order=sort_order)
    db.session.add(t)
    db.session.commit()
    return redirect(url_for("week_detail", week_id=week.id))
