# seed.py (project root, next to app.py)
from datetime import date, timedelta

from app import app  # ✅ import the Flask app from app.py
from Onboarding.extensions import db  # ✅ shared SQLAlchemy() instance
from Onboarding.models import Week, Task, StatusEnum
from sqlalchemy.inspection import inspect as sa_inspect


def model_columns(model):
    return set(sa_inspect(model).columns.keys())


with app.app_context():
    print("DB URI       =", app.config["SQLALCHEMY_DATABASE_URI"])

    # Fresh start in dev
    db.drop_all()
    db.create_all()

    from sqlalchemy import inspect

    print("Tables at seed:", inspect(db.engine).get_table_names())

    # ---- Weeks ----
    week_cols = model_columns(Week)
    print("Week columns:", sorted(week_cols))

    def week_kwargs(i: int, start: date, end: date):
        kw = {}
        if "start_date" in week_cols:
            kw["start_date"] = start
        if "end_date" in week_cols:
            kw["end_date"] = end
        if "title" in week_cols:
            kw["title"] = f"Week {i}"
        elif "name" in week_cols:
            kw["name"] = f"Week {i}"
        return kw

    start = date.today()
    w1 = Week(**week_kwargs(1, start, start + timedelta(days=4)))
    w2 = Week(**week_kwargs(2, start + timedelta(days=7), start + timedelta(days=11)))
    w3 = Week(**week_kwargs(3, start + timedelta(days=14), start + timedelta(days=18)))

    db.session.add_all([w1, w2, w3])
    db.session.flush()  # ensure IDs available

    # ---- Tasks ----
    task_cols = model_columns(Task)
    print("Task columns:", sorted(task_cols))

    def _status_value(s):
        try:
            return s.value  # Enum case
        except AttributeError:
            return s or ""  # string/None case

    def task_kwargs(
        *, week_id, goal, topic, status, notes=None, sort_order=None, title=None
    ):
        kw = {}
        if "week_id" in task_cols:
            kw["week_id"] = week_id
        if "goal" in task_cols and goal is not None:
            kw["goal"] = goal
        if "topic" in task_cols and topic is not None:
            kw["topic"] = topic
        if "title" in task_cols and title is not None:
            kw["title"] = title
        if "status" in task_cols and status is not None:
            kw["status"] = _status_value(status)
        if "notes" in task_cols:
            kw["notes"] = (notes or "").strip()
        if "sort_order" in task_cols and sort_order is not None:
            kw["sort_order"] = sort_order
        return kw

    t1 = Task(
        **task_kwargs(
            week_id=w1.id,
            goal="Job shadow Joel (his desk)",
            topic="Topic: Communication/outage alarms",
            status=StatusEnum.NOT_STARTED,
            notes="Schedule meetings and catchups, if possible.",
            sort_order=0,
        )
    )
    t2 = Task(
        **task_kwargs(
            week_id=w1.id,
            goal="Job Shadow Jennifer (her desk)",
            topic=(
                "Topics:\n"
                " Overview of flow of an incident\n"
                " Organizations\n"
                " Facilities\n"
                " Call Reports\n"
                " Service Orders\n"
                " Test systems\n"
                " Hiring examples"
            ),
            status=StatusEnum.IN_PROGRESS,
            notes="",
            sort_order=1,
        )
    )
    t3 = Task(
        **task_kwargs(
            week_id=w1.id,
            goal="Musco Learning:",
            topic=(
                "Introduction to Musco Learning\n"
                " Basic Lighting Terms\n"
                " Musco Solutions - 5 Easy Pieces Deep Dive Series\n"
                " Email Account and Phishing Awareness\n"
                " Note: If you don’t make it through all eLearning courses listed "
                "for the week, don’t sweat it. Just keep plugging away in the "
                "order of your learning plan."
            ),
            status=StatusEnum.COMPLETE,
            notes=(
                "Select your session for the following in-person classes:\n"
                " Who is Musco\n"
                " What is Light\n"
                " Respectful Workplace for New Hires\n"
                " Intellectual Property Training/Excellence"
            ),
            sort_order=2,
        )
    )

    db.session.add_all([t1, t2, t3])
    db.session.commit()

    print(f"✅ Seed complete! Weeks: {Week.query.count()}, Tasks: {Task.query.count()}")
