from __future__ import annotations
import os
import re
from datetime import date
from flask import Blueprint, render_template, redirect, url_for, request
from Onboarding.extensions import db
from Onboarding.models import (
    OnboardingTemplate,
    TemplateSection,
    TemplateTask,
    TemplateStatusEnum,
    ResponsiblePartyEnum,
    DueTypeEnum,
)
from Onboarding.utils.user_service import current_user, require_builder_or_admin

bp = Blueprint("templates", __name__)


@bp.get("/templates")
def templates_dashboard():
    user = current_user()
    require_builder_or_admin(user)
    templates = OnboardingTemplate.query.order_by(
        OnboardingTemplate.status.asc(),
        OnboardingTemplate.name.asc(),
    ).all()
    return render_template("templates_dashboard.html", user=user, templates=templates)


@bp.get("/templates/import")
def import_template_form():
    user = current_user()
    require_builder_or_admin(user)
    return render_template("template_import.html", user=user)


@bp.post("/templates/import")
def import_template_process():
    user = current_user()
    require_builder_or_admin(user)
    if "file" not in request.files:
        return redirect(request.url)
    f = request.files["file"]
    if not f.filename:
        return redirect(request.url)
    if not (f.filename.lower().endswith(".docx") or f.filename.lower().endswith(".dotx")):
        return (
            render_template("template_import.html", user=user,
                            error="Invalid file type. Please upload a .docx file."),
            400,
        )
    try:
        import docx
    except ImportError:
        return (
            render_template("template_import.html", user=user,
                            error="Server missing 'python-docx' library. Please install it."),
            500,
        )
    try:
        base_name = os.path.splitext(os.path.basename(f.filename))[0]
        tpl = OnboardingTemplate(
            name=f"Imported: {base_name}",
            description=f"Imported from {f.filename} on {date.today()}",
            status=TemplateStatusEnum.DRAFT.value,
            created_by_id=user.id,
        )
        db.session.add(tpl)
        db.session.flush()
        doc = docx.Document(f)
        week_pattern = re.compile(r"Week\s+(\d+)", re.IGNORECASE)
        section_order = 1
        for table in doc.tables:
            if not table.rows:
                continue
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
            title = f"Section {section_order}"
            offset_days = (section_order - 1) * 7
            if header_row_index > 0:
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
            section = TemplateSection(
                template_id=tpl.id,
                title=title,
                offset_days=offset_days,
                order_index=section_order,
            )
            db.session.add(section)
            db.session.flush()
            section_order += 1
            task_order = 1
            for row in table.rows[header_row_index + 1:]:
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
        return redirect(url_for("templates.preview_template", template_id=tpl.id))
    except Exception as e:
        db.session.rollback()
        from flask import current_app
        current_app.logger.error(f"Import failed: {e}")
        return (
            render_template("template_import.html", user=user,
                            error=f"Error processing file: {str(e)}"),
            500,
        )


@bp.route("/templates/<int:template_id>/preview", methods=["GET", "POST"])
def preview_template(template_id: int):
    from sqlalchemy.orm import selectinload
    user = current_user()
    require_builder_or_admin(user)
    tpl = (
        OnboardingTemplate.query.options(
            selectinload(OnboardingTemplate.sections).selectinload(TemplateSection.tasks)
        )
        .filter_by(id=template_id)
        .first_or_404()
    )
    if request.method == "POST":
        new_name = request.form.get("name", "").strip()
        if new_name:
            tpl.name = new_name
        for section in tpl.sections:
            key = f"section_{section.id}_title"
            new_sec_title = request.form.get(key, "").strip()
            if new_sec_title:
                section.title = new_sec_title
        db.session.commit()
        return redirect(url_for("templates.edit_template", template_id=tpl.id))
    return render_template("template_import_preview.html", user=user, template=tpl)


@bp.post("/templates/<int:template_id>/cancel_import")
def cancel_import(template_id: int):
    user = current_user()
    require_builder_or_admin(user)
    tpl = OnboardingTemplate.query.get_or_404(template_id)
    db.session.delete(tpl)
    db.session.commit()
    return redirect(url_for("templates.templates_dashboard"))


@bp.get("/templates/new")
def new_template_form():
    user = current_user()
    require_builder_or_admin(user)
    return render_template("template_new.html", user=user)


@bp.post("/templates")
def create_template():
    user = current_user()
    require_builder_or_admin(user)
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    if not name:
        return (
            render_template("template_new.html", user=user,
                            error="Template name is required.",
                            form={"name": name, "description": description}),
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
    return redirect(url_for("templates.edit_template", template_id=tpl.id))


@bp.get("/templates/<int:template_id>/edit")
def edit_template(template_id: int):
    from sqlalchemy.orm import selectinload
    user = current_user()
    require_builder_or_admin(user)
    tpl = (
        OnboardingTemplate.query.options(
            selectinload(OnboardingTemplate.sections).selectinload(TemplateSection.tasks)
        )
        .filter_by(id=template_id)
        .first_or_404()
    )
    return render_template("template_edit.html", user=user, template=tpl)


@bp.post("/templates/<int:template_id>/sections")
def add_template_section(template_id: int):
    user = current_user()
    require_builder_or_admin(user)
    tpl = OnboardingTemplate.query.get_or_404(template_id)
    title = (request.form.get("title") or "").strip()
    if not title:
        return redirect(url_for("templates.edit_template", template_id=template_id))
    offset_raw = (request.form.get("offset_days") or "").strip()
    try:
        offset_days = int(offset_raw) if offset_raw else None
    except ValueError:
        offset_days = None
    order_index = (max(s.order_index or 0 for s in tpl.sections) + 1) if tpl.sections else 1
    section = TemplateSection(
        template_id=tpl.id,
        title=title,
        description=(request.form.get("description") or "").strip() or None,
        offset_days=offset_days,
        order_index=order_index,
    )
    db.session.add(section)
    db.session.commit()
    return redirect(url_for("templates.edit_template", template_id=template_id))


@bp.post("/templates/<int:template_id>/sections/<int:section_id>/delete")
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
    return redirect(url_for("templates.edit_template", template_id=template_id))


@bp.post("/templates/<int:template_id>/sections/<int:section_id>/tasks")
def add_template_task(template_id: int, section_id: int):
    user = current_user()
    require_builder_or_admin(user)
    section = TemplateSection.query.filter_by(
        id=section_id, template_id=template_id
    ).first_or_404()
    title = (request.form.get("title") or "").strip()
    if not title:
        return redirect(url_for("templates.edit_template", template_id=template_id))
    responsible_raw = (request.form.get("responsible_party") or "new_hire").strip()
    if responsible_raw not in {"new_hire", "manager", "other"}:
        responsible_raw = "new_hire"
    due_type_raw = (request.form.get("due_type") or "days_from_start").strip()
    if due_type_raw not in {e.value for e in DueTypeEnum}:
        due_type_raw = DueTypeEnum.DAYS_FROM_START.value
    offset_raw = (request.form.get("offset_days") or "").strip()
    section_day_raw = (request.form.get("section_day") or "").strip()
    try:
        offset_days = int(offset_raw) if offset_raw and due_type_raw == DueTypeEnum.DAYS_FROM_START.value else None
    except ValueError:
        offset_days = None
    try:
        section_day = int(section_day_raw) if section_day_raw and due_type_raw == DueTypeEnum.DAY_WITHIN_SECTION.value else None
    except ValueError:
        section_day = None
    order_index = (max(t.order_index or 0 for t in section.tasks) + 1) if section.tasks else 1
    t = TemplateTask(
        section_id=section.id,
        title=title,
        description=(request.form.get("description") or "").strip() or None,
        responsible_party=responsible_raw,
        due_type=due_type_raw,
        offset_days=offset_days,
        section_day=section_day,
        category=(request.form.get("category") or "").strip() or None,
        is_required=bool(request.form.get("is_required")),
        order_index=order_index,
    )
    db.session.add(t)
    db.session.commit()
    return redirect(url_for("templates.edit_template", template_id=template_id))


@bp.get("/templates/<int:template_id>/sections/<int:section_id>/tasks/<int:task_id>/edit")
def edit_template_task(template_id: int, section_id: int, task_id: int):
    user = current_user()
    require_builder_or_admin(user)
    section = TemplateSection.query.filter_by(
        id=section_id, template_id=template_id
    ).first_or_404()
    task = TemplateTask.query.filter_by(id=task_id, section_id=section.id).first_or_404()
    tpl = OnboardingTemplate.query.get_or_404(template_id)
    return render_template("template_task_edit.html", user=user, template=tpl,
                           section=section, task=task)


@bp.post("/templates/<int:template_id>/sections/<int:section_id>/tasks/<int:task_id>")
def update_template_task(template_id: int, section_id: int, task_id: int):
    user = current_user()
    require_builder_or_admin(user)
    section = TemplateSection.query.filter_by(
        id=section_id, template_id=template_id
    ).first_or_404()
    task = TemplateTask.query.filter_by(id=task_id, section_id=section.id).first_or_404()
    title = (request.form.get("title") or "").strip()
    if not title:
        return redirect(url_for("templates.edit_template_task",
                                template_id=template_id, section_id=section_id, task_id=task_id))
    responsible_raw = (request.form.get("responsible_party") or "new_hire").strip()
    if responsible_raw not in {"new_hire", "manager", "other"}:
        responsible_raw = "new_hire"
    due_type_raw = (request.form.get("due_type") or "days_from_start").strip()
    if due_type_raw not in {"days_from_start", "day_within_section"}:
        due_type_raw = "days_from_start"
    offset_raw = (request.form.get("offset_days") or "").strip()
    section_day_raw = (request.form.get("section_day") or "").strip()
    offset_days = None
    if due_type_raw == "days_from_start":
        try:
            offset_days = int(offset_raw) if offset_raw else None
        except ValueError:
            pass
    section_day = None
    if due_type_raw == "day_within_section":
        try:
            section_day = int(section_day_raw) if section_day_raw else None
        except ValueError:
            pass
    task.title = title
    task.description = (request.form.get("description") or "").strip() or None
    task.responsible_party = responsible_raw
    task.due_type = due_type_raw
    task.offset_days = offset_days
    task.section_day = section_day
    task.category = (request.form.get("category") or "").strip() or None
    task.is_required = bool(request.form.get("is_required"))
    db.session.commit()
    return redirect(url_for("templates.edit_template", template_id=template_id))


@bp.post("/templates/<int:template_id>/sections/<int:section_id>/tasks/<int:task_id>/delete")
def delete_template_task(template_id: int, section_id: int, task_id: int):
    user = current_user()
    require_builder_or_admin(user)
    section = TemplateSection.query.filter_by(
        id=section_id, template_id=template_id
    ).first_or_404()
    task = TemplateTask.query.filter_by(id=task_id, section_id=section.id).first_or_404()
    db.session.delete(task)
    db.session.commit()
    return redirect(url_for("templates.edit_template", template_id=template_id))


@bp.post("/templates/<int:template_id>/publish")
def publish_template(template_id: int):
    user = current_user()
    require_builder_or_admin(user)
    tpl = OnboardingTemplate.query.get_or_404(template_id)
    if tpl.status != TemplateStatusEnum.RETIRED.value:
        tpl.status = TemplateStatusEnum.PUBLISHED.value
        db.session.commit()
    return redirect(url_for("templates.templates_dashboard"))


@bp.post("/templates/<int:template_id>/retire")
def retire_template(template_id: int):
    user = current_user()
    require_builder_or_admin(user)
    tpl = OnboardingTemplate.query.get_or_404(template_id)
    if tpl.status != TemplateStatusEnum.RETIRED.value:
        tpl.status = TemplateStatusEnum.RETIRED.value
        db.session.commit()
    return redirect(url_for("templates.templates_dashboard"))


@bp.post("/templates/<int:template_id>/delete")
def delete_template(template_id: int):
    user = current_user()
    require_builder_or_admin(user)
    tpl = OnboardingTemplate.query.get_or_404(template_id)
    db.session.delete(tpl)
    db.session.commit()
    return redirect(url_for("templates.templates_dashboard"))
