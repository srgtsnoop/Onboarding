"""Backfill weeks.owner_user_id / manager_user_id from the owning user.

Weeks created before per-user ownership was added have NULL
owner_user_id / manager_user_id, which causes the task edit routes
(status, notes, due date) to 403 even though the read-only week view
works fine (it doesn't check ownership).
"""

from pathlib import Path
import sys

from sqlalchemy import text

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import app
from Onboarding.extensions import db


def upgrade():
    with app.app_context():
        conn = db.engine.connect()

        result = conn.execute(
            text(
                """
                SELECT w.id, u.id, u.manager_id
                FROM weeks w
                JOIN users u ON u.onboarding_plan_id = w.onboarding_plan_id
                WHERE w.owner_user_id IS NULL OR w.manager_user_id IS NULL
                """
            )
        )
        rows = result.fetchall()

        for week_id, owner_id, manager_id in rows:
            conn.execute(
                text(
                    """
                    UPDATE weeks
                    SET owner_user_id = :owner_id, manager_user_id = :manager_id
                    WHERE id = :week_id
                    """
                ),
                {"owner_id": owner_id, "manager_id": manager_id, "week_id": week_id},
            )

        conn.commit()
        conn.close()
        print(f"Backfilled owner/manager on {len(rows)} week(s).")


if __name__ == "__main__":
    upgrade()
    print("✅ Migration complete")
