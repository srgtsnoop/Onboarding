from __future__ import annotations
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request
from Onboarding.extensions import db
from Onboarding.models import Task, StatusEnum
from Onboarding.utils.user_service import (
    current_user,
    resolve_principal,
    ensure_week_for_user,
    ensure_task_for_user,
)
from Onboarding.policy import ensure_week_access

bp = Blueprint("tasks", __name__)


def _parse_due_date(s: str):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


@bp.post("/weeks/<int:week_id>/tasks")
def add_task(week_id: int):
    user = current_user()
    principal = resolve_principal(user)
    w = ensure_week_for_user(week_id, user)
    ensure_week_access(principal, w)
    title = request.form.get("goal", "").strip()
    topic = request.form.get("topic", "").strip()
    notes = request.form.get("notes", "").strip()
    due_raw = request.form.get("due_date", "").strip()
    due_date = None
    if due_raw:
        try:
            due_date = datetime.strptime(due_raw, "%Y-%m-%d").date()
        except ValueError:
            pass
    t = Task(
        week_id=w.id,
        title=title,
        topic=topic,
        notes=notes,
        sort_order=(Task.query.filter_by(week_id=w.id).count() + 1),
    )
    if hasattr(Task, "due_date"):
        t.due_date = due_date
    db.session.add(t)
    db.session.commit()
    return redirect(url_for("weeks.week_detail", week_id=w.id, as_user=user.email))


@bp.get("/tasks/<int:task_id>/notes/edit", endpoint="edit_notes_form")
def edit_notes_form(task_id):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    return render_template("_task_notes_form.html", t=t, user=user)


@bp.post("/tasks/<int:task_id>/notes", endpoint="update_notes")
def update_notes(task_id):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    t.notes = (request.form.get("notes") or "").strip()
    db.session.commit()
    if request.headers.get("HX-Request"):
        return render_template("_task_notes_display.html", t=t, user=user)
    return redirect(url_for("weeks.week_detail", week_id=t.week_id, as_user=user.email))


@bp.get("/tasks/<int:task_id>/notes/cancel", endpoint="cancel_notes_edit")
def cancel_notes_edit(task_id):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    return render_template("_task_notes_display.html", t=t, user=user)


@bp.get("/tasks/<int:task_id>/due-date/form", endpoint="edit_date_form")
def edit_date_form(task_id: int):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    return render_template("_task_date_form.html", t=t, user=user)


@bp.get("/tasks/<int:task_id>/due-date/display", endpoint="date_display")
def date_display(task_id: int):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    return render_template("_task_date_display.html", t=t, user=user)


@bp.post("/tasks/<int:task_id>/due-date", endpoint="update_due_date")
def update_due_date(task_id: int):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    if request.form.get("clear") == "1":
        t.due_date = None
        db.session.commit()
        if request.headers.get("HX-Request"):
            return render_template("_task_date_display.html", t=t)
        return redirect(url_for("weeks.week_detail", week_id=t.week_id, as_user=user.email))
    raw = (request.form.get("due_date") or request.form.get("due_date_text") or "").strip()
    parsed = _parse_due_date(raw)
    if raw and parsed is None:
        return render_template("_task_date_form.html", t=t, error="Enter a valid date (MM/DD/YY)."), 400
    t.due_date = parsed
    db.session.commit()
    return render_template("_task_date_display.html", t=t, user=user)


@bp.get("/tasks/<int:task_id>/status/edit", endpoint="edit_status_form")
def edit_status_form(task_id: int):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    return render_template("_task_status_form.html", t=t, user=user)


@bp.get("/tasks/<int:task_id>/status/view", endpoint="view_status")
def view_status(task_id: int):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    return render_template("_task_status_display.html", t=t, user=user)


@bp.post("/tasks/<int:task_id>/status", endpoint="update_status")
def update_status(task_id: int):
    user = current_user()
    principal = resolve_principal(user)
    t = ensure_task_for_user(task_id, user)
    ensure_week_access(principal, t.week)
    label = (request.form.get("status") or "").strip()
    valid_values = [e.value for e in StatusEnum]
    if label not in valid_values:
        if request.headers.get("HX-Request"):
            return render_template("_task_status_form.html", t=t, error="Invalid status"), 400
        return redirect(url_for("weeks.week_detail", week_id=t.week_id, as_user=user.email))
    t.status = StatusEnum(label)
    db.session.commit()
    if request.headers.get("HX-Request"):
        return render_template("_task_status_display.html", t=t, user=user)
    return redirect(url_for("weeks.week_detail", week_id=t.week_id, as_user=user.email))


@bp.get("/tasks/<int:task_id>/status/cancel")
def cancel_status_edit(task_id):
    user = current_user()
    t = ensure_task_for_user(task_id, user)
    return render_template("_task_status_display.html", t=t, user=user)
