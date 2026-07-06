"""Application entry point: app creation, config, context processors,
blueprint registration. Route logic lives in Onboarding/routes/*."""
from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, redirect, session, url_for

from Onboarding.extensions import db

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.join(BASE_DIR, "Onboarding")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(PROJ_DIR, "static")
INSTANCE_DIR = os.path.join(PROJ_DIR, "instance")

os.makedirs(INSTANCE_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# App + config
# ---------------------------------------------------------------------------
app = Flask(
    __name__,
    template_folder=TEMPLATES_DIR,
    static_folder=STATIC_DIR,
    instance_path=INSTANCE_DIR,
)

DB_PATH = Path(BASE_DIR, "db.sqlite3").as_posix()
# Tests set this env var (see conftest.py) before this module is first
# imported, since the SQLAlchemy engine binds to whatever URI is in
# app.config at that point and does NOT rebind on later config changes
# (db.engine.dispose() only closes pooled connections, not the bound URL).
app.config.update(
    SQLALCHEMY_DATABASE_URI=os.environ.get(
        "SQLALCHEMY_DATABASE_URI", f"sqlite:///{DB_PATH}"
    ),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret"),
)

db.init_app(app)

# Import models so tables register on this db metadata before create_all.
import Onboarding.models as _models  # noqa: E402,F401

with app.app_context():
    db.create_all()

# ---------------------------------------------------------------------------
# Blueprints
# ---------------------------------------------------------------------------
from Onboarding.routes import admin, manager, tasks, templates, weeks  # noqa: E402

app.register_blueprint(weeks.bp)
app.register_blueprint(tasks.bp)
app.register_blueprint(templates.bp)
app.register_blueprint(manager.bp)
app.register_blueprint(admin.bp)

# ---------------------------------------------------------------------------
# Context processors
# ---------------------------------------------------------------------------
from Onboarding.models import User  # noqa: E402
from Onboarding.utils.markdown import render_markdown  # noqa: E402
from Onboarding.utils.user_service import current_user  # noqa: E402


@app.context_processor
def inject_markdown_renderer():
    return dict(render_markdown=render_markdown)


@app.context_processor
def inject_user():
    u = session.get("user")
    return {"user": u or {"role": "guest"}}


@app.context_processor
def inject_user_switcher():
    users = User.query.order_by(User.role.asc(), User.full_name.asc()).all()
    try:
        active_user = current_user()
    except Exception:
        active_user = None

    return {
        "switchable_users": users,
        "nav_current_user": active_user,
    }


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return redirect(url_for("weeks.weeks"))


if __name__ == "__main__":
    app.run(debug=True)
