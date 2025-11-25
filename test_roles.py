import os
import tempfile

import pytest

from app import app
from Onboarding.extensions import db
from Onboarding.models import OnboardingPlan, RoleEnum, User, Week


@pytest.fixture()
def client_with_data():
    db_fd, db_path = tempfile.mkstemp()
    app.config.update(SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_path}", TESTING=True)

    with app.app_context():
        db.engine.dispose()
        db.drop_all()
        db.create_all()

        plan_alpha = OnboardingPlan(name="Alpha Plan")
        plan_beta = OnboardingPlan(name="Beta Plan")
        db.session.add_all([plan_alpha, plan_beta])
        db.session.flush()

        alpha_week = Week(title="Alpha Week 1", onboarding_plan_id=plan_alpha.id)
        beta_week = Week(title="Beta Week 1", onboarding_plan_id=plan_beta.id)
        db.session.add_all([alpha_week, beta_week])

        admin = User(
            full_name="Avery Admin",
            email="admin@test.com",
            role=RoleEnum.ADMIN.value,
            onboarding_plan_id=plan_alpha.id,
        )
        manager = User(
            full_name="Morgan Manager",
            email="manager@test.com",
            role=RoleEnum.MANAGER.value,
            onboarding_plan_id=plan_alpha.id,
        )
        report_alpha = User(
            full_name="Uma User",
            email="user-alpha@test.com",
            role=RoleEnum.USER.value,
            onboarding_plan_id=plan_alpha.id,
            manager=manager,
        )
        report_beta = User(
            full_name="Uri User",
            email="user-beta@test.com",
            role=RoleEnum.USER.value,
            onboarding_plan_id=plan_beta.id,
            manager=manager,
        )
        outsider = User(
            full_name="Other User",
            email="outsider@test.com",
            role=RoleEnum.USER.value,
            onboarding_plan_id=plan_beta.id,
        )

        db.session.add_all([admin, manager, report_alpha, report_beta, outsider])
        db.session.commit()

        fixture_data = {
            "plan_ids": {"alpha": plan_alpha.id, "beta": plan_beta.id},
            "users": {
                "admin": admin.email,
                "manager": manager.email,
                "report_alpha": report_alpha.email,
                "report_beta": report_beta.email,
                "outsider": outsider.email,
            },
        }

    with app.test_client() as client:
        yield client, fixture_data

    os.close(db_fd)
    os.remove(db_path)


def test_user_plan_is_scoped_to_self(client_with_data):
    client, data = client_with_data

    resp = client.get(
        "/api/my-plan", headers={"X-User-Email": data["users"]["report_alpha"]}
    )
    assert resp.status_code == 200

    payload = resp.get_json()
    week_titles = {week["title"] for week in payload["plan"]["weeks"]}

    assert week_titles == {"Alpha Week 1"}
    assert payload["user"]["email"] == data["users"]["report_alpha"]


def test_manager_sees_only_direct_reports(client_with_data):
    client, data = client_with_data

    resp = client.get(
        "/api/manager/reports",
        headers={"X-User-Email": data["users"]["manager"]},
    )
    assert resp.status_code == 200

    payload = resp.get_json()
    emails = {u["email"] for u in payload["direct_reports"]}

    assert emails == {
        data["users"]["report_alpha"],
        data["users"]["report_beta"],
    }


def test_non_manager_cannot_use_manager_report(client_with_data):
    client, data = client_with_data

    resp = client.get(
        "/api/manager/reports",
        headers={"X-User-Email": data["users"]["report_alpha"]},
    )
    assert resp.status_code == 403


def test_admin_overview_includes_everyone(client_with_data):
    client, data = client_with_data

    resp = client.get(
        "/api/admin/overview", headers={"X-User-Email": data["users"]["admin"]}
    )
    assert resp.status_code == 200

    payload = resp.get_json()
    user_emails = {u["email"] for u in payload["users"]}

    assert user_emails == set(data["users"].values())
    plan_ids = {p["id"] for p in payload["plans"]}
    assert plan_ids == {data["plan_ids"]["alpha"], data["plan_ids"]["beta"]}
