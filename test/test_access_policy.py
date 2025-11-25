import os
import tempfile

from flask import Response

from app import app
from Onboarding.extensions import db
from Onboarding.models import Week


def _make_week(title: str, owner: int, manager: int | None):
    w = Week(title=title, owner_user_id=owner, manager_user_id=manager)
    db.session.add(w)
    return w


def _get(client, path: str, user_id: int | None, role: str) -> Response:
    headers = {"X-User-Role": role}
    if user_id is not None:
        headers["X-User-Id"] = str(user_id)
    return client.get(path, headers=headers)


def setup_function(_):
    with app.app_context():
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{path}"
        db.engine.dispose()
        db.drop_all()
        db.create_all()


class TestAccessPolicy:
    def test_user_can_only_view_own_week(self):
        with app.app_context():
            own = _make_week("User Week", owner=1, manager=2)
            other = _make_week("Other Week", owner=3, manager=2)
            db.session.commit()
            own_id, other_id = own.id, other.id

        client = app.test_client()

        resp = _get(client, f"/weeks/{own_id}", user_id=1, role="user")
        assert resp.status_code == 200
        assert b"User Week" in resp.data

        resp_other = _get(client, f"/weeks/{other_id}", user_id=1, role="user")
        assert resp_other.status_code == 403

        list_resp = _get(client, "/weeks", user_id=1, role="user")
        assert b"User Week" in list_resp.data
        assert b"Other Week" not in list_resp.data

    def test_manager_sees_direct_reports(self):
        with app.app_context():
            managed = _make_week("Managed", owner=4, manager=10)
            also_self = _make_week("Manager Self", owner=10, manager=10)
            not_managed = _make_week("Not Managed", owner=5, manager=11)
            db.session.commit()
            managed_id, self_id, not_managed_id = (
                managed.id,
                also_self.id,
                not_managed.id,
            )

        client = app.test_client()

        resp = _get(client, f"/weeks/{managed_id}", user_id=10, role="manager")
        assert resp.status_code == 200
        assert b"Managed" in resp.data

        resp_self = _get(client, f"/weeks/{self_id}", user_id=10, role="manager")
        assert resp_self.status_code == 200

        resp_blocked = _get(
            client, f"/weeks/{not_managed_id}", user_id=10, role="manager"
        )
        assert resp_blocked.status_code == 403

        list_resp = _get(client, "/weeks", user_id=10, role="manager")
        assert b"Managed" in list_resp.data
        assert b"Manager Self" in list_resp.data
        assert b"Not Managed" not in list_resp.data

    def test_admin_can_view_all(self):
        with app.app_context():
            w1 = _make_week("Any Week 1", owner=6, manager=7)
            w2 = _make_week("Any Week 2", owner=8, manager=9)
            db.session.commit()
            w1_id, w2_id = w1.id, w2.id

        client = app.test_client()

        resp1 = _get(client, f"/weeks/{w1_id}", user_id=None, role="admin")
        resp2 = _get(client, f"/weeks/{w2_id}", user_id=None, role="admin")

        assert resp1.status_code == 200
        assert resp2.status_code == 200

        list_resp = _get(client, "/weeks", user_id=None, role="admin")
        assert b"Any Week 1" in list_resp.data
        assert b"Any Week 2" in list_resp.data
