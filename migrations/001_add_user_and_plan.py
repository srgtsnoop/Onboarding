"""Simple migration script to add onboarding plans and users."""

from pathlib import Path
import sys

from sqlalchemy import text

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import app
from Onboarding.extensions import db


def column_exists(table_name: str, column_name: str) -> bool:
    result = db.session.execute(text(f"PRAGMA table_info({table_name})"))
    return any(row[1] == column_name for row in result)


def table_exists(table_name: str) -> bool:
    result = db.session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'")
    )
    return any(row[0] == table_name for row in result)


def upgrade():
    with app.app_context():
        conn = db.engine.connect()
        conn.execute(text("PRAGMA foreign_keys=ON"))

        # Create onboarding_plans table if missing
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS onboarding_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(255) NOT NULL DEFAULT 'Onboarding Plan',
                    description TEXT
                )
                """
            )
        )

        # Add onboarding_plan_id to weeks if not present
        if table_exists("weeks") and not column_exists("weeks", "onboarding_plan_id"):
            conn.execute(
                text("ALTER TABLE weeks ADD COLUMN onboarding_plan_id INTEGER")
            )

        # Create users table if missing
        if not table_exists("users"):
            conn.execute(
                text(
                    """
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        email VARCHAR(255) NOT NULL UNIQUE,
                        full_name VARCHAR(255) NOT NULL,
                        role VARCHAR(32) NOT NULL DEFAULT 'user',
                        onboarding_plan_id INTEGER,
                        manager_id INTEGER,
                        FOREIGN KEY(onboarding_plan_id) REFERENCES onboarding_plans(id),
                        FOREIGN KEY(manager_id) REFERENCES users(id)
                    )
                    """
                )
            )

        conn.commit()
        conn.close()


if __name__ == "__main__":
    upgrade()
    print("✅ Migration complete")
