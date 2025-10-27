from __future__ import annotations
from datetime import date
from app import app
from models import db, Week, Task, StatusEnum

W1_TASKS = [
    {
        "goal": "Job shadow Joel (his desk)",
        "topic": "Topic: Communication/outage alarms",
        "due_date": date(2025, 10, 8),
    },
    {
        "goal": "Job shadow Jennifer (her desk)",
        "topic": "Overview of flow of incident\nOrganizations\nFacilities\nCall Reports\nService Orders\nTest systems\nHiring examples",
        "due_date": date(2025, 10, 8),
    },
    {
        "goal": "Job shadow a Warranty Rep",
        "topic": "Glimpse into the day-to-day operations",
        "due_date": date(2025, 10, 8),
        "notes": "Ask your supervisor who to shadow",
    },
    {
        "goal": "Job shadow a Warranty Rep",
        "topic": "Process of communication/outage alarms",
        "due_date": date(2025, 10, 9),
        "notes": "Ask your supervisor who to shadow",
    },
    {
        "goal": "Job shadow a Technical Specialist",
        "topic": "Understanding of their role & how both roles work together",
        "due_date": date(2025, 10, 9),
        "notes": "Ask your supervisor who to shadow",
    },
    {
        "goal": "Meet with Jennifer (her desk)",
        "topic": "Scoreboard shortcuts\nUsing the Calendar\nBookmarks, etc.",
        "due_date": date(2025, 10, 9),
    },
    {
        "goal": "Meet with Joel (his desk)",
        "topic": "Communication/outage alarms",
        "due_date": date(2025, 10, 10),
    },
    {
        "goal": "Job shadow a Warranty Rep to review",
        "topic": "How to use the Calendar in Scoreboard\nReview of the Facility screen in Scoreboard\nExamples of hiring work",
        "due_date": date(2025, 10, 10),
        "notes": "Ask your supervisor who to shadow",
    },
    {
        "goal": "Meet with a Warranty Rep to review Engineering Docs",
        "topic": "Aiming diagrams\nScans\nEwork\nProduction docs (research - how to find answers)",
        "due_date": date(2025, 10, 10),
        "notes": "Ask your supervisor who to shadow",
    },
    {
        "goal": "Test calls",
        "topic": "Once you’ve taken the 8x8 eLearning, ask someone in your pod to test calls with you. Testing calls will ensure your headset is working properly.",
        "due_date": date(2025, 10, 10),
    },
    {
        "goal": "Musco Learning",
        "topic": "Introduction to Musco Learning - I’m Ready to Learn!\nBasic Lighting Terms\nMusco Solutions - 5 Easy Pieces Deep Dive Series\nEmail Account and Phishing Awareness",
        "due_date": date(2025, 10, 10),
        "notes": "Select your session for in-person classes: Who is Musco; What is Light; Respectful Workplace for New Hires; Intellectual Property Training/Excellence",
    },
]


def coerce_status(val):
    """Accept StatusEnum or common legacy strings and return a StatusEnum."""
    if isinstance(val, StatusEnum):
        return val
    if isinstance(val, str):
        mapping = {
            "Not Started": StatusEnum.NOT_STARTED,
            "In Progress": StatusEnum.IN_PROGRESS,
            "Complete": StatusEnum.COMPLETE,
            "TODO": StatusEnum.NOT_STARTED,
            "TO_START": StatusEnum.NOT_STARTED,
            "DONE": StatusEnum.COMPLETE,
        }
        return mapping.get(val, StatusEnum.NOT_STARTED)
    return StatusEnum.NOT_STARTED


if __name__ == "__main__":
    print("Seeding using DB URI:", app.config.get("SQLALCHEMY_DATABASE_URI"))
    with app.app_context():
        db.drop_all()
        db.create_all()

        w1 = Week(
            name="Week 1", start_date=date(2025, 10, 6), end_date=date(2025, 10, 10)
        )
        db.session.add(w1)

        for i, t in enumerate(W1_TASKS):
            task = Task(
                goal=t["goal"],
                topic=t.get("topic"),
                due_date=t.get("due_date"),
                notes=t.get("notes"),
                sort_order=i,
                week=w1,
                status=coerce_status(t.get("status", StatusEnum.NOT_STARTED)),
            )
            db.session.add(task)

        db.session.commit()
        print("Seeded Week 1 with", len(W1_TASKS), "tasks")
