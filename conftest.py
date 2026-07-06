"""Root conftest.py: point the whole test session at a throwaway SQLite file.

This MUST run before any test module does ``from app import app``, because
app.py's Flask-SQLAlchemy engine binds to whatever SQLALCHEMY_DATABASE_URI
is in app.config the first time the engine is created, and never rebinds to
a different URI afterward -- db.engine.dispose() only closes pooled
connections, it does not repoint the engine at a new database. Without this,
per-test fixtures that call app.config.update(SQLALCHEMY_DATABASE_URI=...)
silently no-op, and db.drop_all()/db.create_all() run against the real
db.sqlite3 instead of a temp file.

conftest.py at the repo root is collected by pytest before any test file
anywhere under the root (including test_roles.py and test/*.py), so setting
the env var here, at import time, is early enough.
"""
import atexit
import os
import tempfile

_fd, _path = tempfile.mkstemp(suffix=".sqlite3")
os.close(_fd)
os.environ["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{_path}"


def _cleanup():
    try:
        os.remove(_path)
    except OSError:
        pass  # e.g. Windows file lock from a still-open sqlite connection


atexit.register(_cleanup)
