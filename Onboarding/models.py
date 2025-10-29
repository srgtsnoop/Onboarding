# models.py
# Onboarding/models.py
from enum import Enum
from datetime import date
from .extensions import db  # <-- the only db import; shared instance


# -------------------------------------------------------------------
# ENUM: Defines the valid task statuses
# -------------------------------------------------------------------
class StatusEnum(str, Enum):
    NOT_STARTED = "Not Started"
    IN_PROGRESS = "In Progress"
    COMPLETE = "Complete"


# -------------------------------------------------------------------
# MODEL: Week (a collection of tasks)
# -------------------------------------------------------------------
class Week(db.Model):
    __tablename__ = "weeks"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False, default="Week")
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)

    # ✅ Use back_populates (no backref)
    tasks = db.relationship(
        "Task",
        back_populates="week",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="Task.sort_order.asc().nullsfirst(), Task.id.asc()",
    )


# -------------------------------------------------------------------
# MODEL: Task (individual onboarding items)
# -------------------------------------------------------------------
class Task(db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)

    # --- FK + relationship (single pair; use back_populates to match Week.tasks) ---
    week_id = db.Column(db.Integer, db.ForeignKey("weeks.id"), nullable=False)
    week = db.relationship("Week", back_populates="tasks")

    # --- Core fields ---
    title = db.Column(db.String(255), nullable=True)
    goal = db.Column(db.String(255), nullable=True)
    topic = db.Column(db.Text, nullable=True)

    # Store the status as a string matching StatusEnum values
    status = db.Column(
        db.String(32), nullable=False, default=StatusEnum.NOT_STARTED.value
    )

    notes = db.Column(db.Text, nullable=False, default="")
    sort_order = db.Column(db.Integer, nullable=True)

    def __repr__(self):
        return f"<Task {self.id}: {self.goal or self.title} [{self.status}]>"

    def is_complete(self):
        return self.status == StatusEnum.COMPLETE.value
