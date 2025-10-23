from __future__ import annotations
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, jsonify, abort
from models import db, Week, Task, StatusEnum


app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///db.sqlite3"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False


# Initialize DB
with app.app_context():
    db.init_app(app)


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
@app.post("/task/<int:task_id>/update-notes")
def update_notes(task_id: int):
    task = Task.query.get_or_404(task_id)
    notes = request.form.get("notes", "").strip()
    task.notes = notes or None
    db.session.commit()
    return render_template("_task_row.html", t=task)


@app.post("/task/<int:task_id>/edit-notes")
def edit_notes(task_id: int):
    task = Task.query.get_or_404(task_id)
    return render_template("_task_notes_form.html", t=task)


@app.post("/task/<int:task_id>/update-date")
def update_date(task_id: int):
    task = Task.query.get_or_404(task_id)
    raw = request.form.get("due_date", "").strip()
    if raw:
        try:
            task.due_date = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            abort(400, "Invalid date")
    else:
        task.due_date = None
    db.session.commit()
    return render_template("_task_row.html", t=task)


@app.post("/task/<int:task_id>/edit-date")
def edit_date(task_id: int):
    task = Task.query.get_or_404(task_id)
    return render_template("_task_date_form.html", t=task)


@app.post("/task/<int:task_id>/update-status")
def update_status(task_id: int):
    task = Task.query.get_or_404(task_id)
    new_status = request.form.get("status")
    if new_status not in [s.value for s in StatusEnum]:
        abort(400, "Invalid status")
    task.status = StatusEnum(new_status)
    db.session.commit()
    return render_template("_task_row.html", t=task)


@app.post("/task/<int:task_id>/edit-status")
def edit_status(task_id: int):
    task = Task.query.get_or_404(task_id)
    return render_template("_task_status_form.html", t=task)


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