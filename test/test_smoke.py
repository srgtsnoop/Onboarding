"""Smoke tests guarding the blueprint structure.

1. Every url_for() endpoint referenced in any template must exist in the
   app's view functions (catches BuildError at test time, not in prod).
2. Every major page renders (2xx) for a role allowed to see it.
"""

import os
import re
import tempfile
from datetime import date
from pathlib import Path

import pytest

from app import app
from Onboarding.extensions import db
from Onboarding.models import (
    OnboardingPlan,
    OnboardingTemplate,
    RoleEnum,
    TemplateSection,
    TemplateStatusEnum,
    TemplateTask,
    User,
    Week,
)

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
URL_FOR_RE = re.compile(r"""url_for\(\s*['"]([^'"]+)['"]""")


def test_every_template_endpoint_exists():
    referenced = set()
    for html in TEMPLATES_DIR.glob("*.html"):
        referenced |= set(URL_FOR_RE.findall(html.read_text()))

    known = set(app.view_functions) | {"static"}
    missing = sorted(referenced - known)
    assert not missing, f"Templates reference unknown endpoints: {missing}"


@pytest.fixture()
def seeded_client():
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    with app.app_context():
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{path}"
        db.engine.dispose()
        db.drop_all()
        db.create_all()

        admin = User(email="a@x.com", full_name="Ada Admin", role=RoleEnum.ADMIN.value)
        builder = User(
            email="b@x.com", full_name="Bea Builder", role=RoleEnum.BUILDER.value
        )
        db.session.add_all([admin, builder])
        db.session.flush()
        mgr = User(email="m@x.com", full_name="Max Manager", role=RoleEnum.MANAGER.value)
        db.session.add(mgr)
        db.session.flush()
        emp = User(
            email="u@x.com",
            full_name="Uma User",
            role=RoleEnum.USER.value,
            manager_id=mgr.id,
        )
        db.session.add(emp)
        db.session.flush()

        plan = OnboardingPlan(name="Plan")
        db.session.add(plan)
        db.session.flush()
        emp.onboarding_plan_id = plan.id
        week = Week(
            title="W1",
            onboarding_plan_id=plan.id,
            owner_user_id=emp.id,
            manager_user_id=mgr.id,
            start_date=date.today(),
        )
        db.session.add(week)

        tpl = OnboardingTemplate(
            name="Tpl",
            status=TemplateStatusEnum.PUBLISHED.value,
            created_by_id=builder.id,
        )
        db.session.add(tpl)
        db.session.flush()
        sec = TemplateSection(
            template_id=tpl.id, title="Week 1", offset_days=0, order_index=1
        )
        db.session.add(sec)
        db.session.flush()
        db.session.add(
            TemplateTask(section_id=sec.id, title="T", order_index=1, section_day=1)
        )
        db.session.commit()

        ids = {"week": week.id, "tpl": tpl.id, "sec": sec.id, "emp": emp.id}

    yield app.test_client(), ids
    try:
        os.unlink(path)
    except OSError:
        pass


PAGES = [
    ("/", "u@x.com"),
    ("/weeks", "u@x.com"),
    ("/weeks/{week}", "u@x.com"),
    ("/api/my-plan", "u@x.com"),
    ("/templates", "b@x.com"),
    ("/templates/import", "b@x.com"),
    ("/templates/new", "b@x.com"),
    ("/templates/{tpl}/edit", "b@x.com"),
    ("/templates/{tpl}/preview", "b@x.com"),
    ("/manager/reports", "m@x.com"),
    ("/api/manager/reports", "m@x.com"),
    ("/manager/plans", "m@x.com"),
    ("/assign", "a@x.com"),
    ("/admin/overview", "a@x.com"),
    ("/api/admin/overview", "a@x.com"),
    ("/healthz", "u@x.com"),
]


def test_all_pages_render(seeded_client):
    client, ids = seeded_client
    for path_tpl, email in PAGES:
        path = path_tpl.format(**ids)
        resp = client.get(path, headers={"X-User-Email": email})
        assert resp.status_code in (200, 302), f"{path} -> {resp.status_code}"
