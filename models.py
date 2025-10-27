# models.py
from flask_sqlalchemy import SQLAlchemy
from enum import Enum
from datetime import date

db = SQLAlchemy()


# -------------------------------------------------------------------
# ENUM: Defines the valid task statuses
# -------------------------------------------------------------------
class StatusEnum(Enum):
    NOT_STARTED = "Not Started"
    IN_PROGRESS = "In Progress"
    COMPLETE = "Complete"


# -------------------------------------------------------------------
# MODEL: Week (a collection of tasks)
# -------------------------------------------------------------------
class Week(db.Model):
    __tablename__ = "weeks"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)

    # Relationship: one week has many tasks
    tasks = db.relationship("Task", back_populates="week", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Week {self.name}>"


# -------------------------------------------------------------------
# MODEL: Task (individual onboarding items)
# -------------------------------------------------------------------
class Task(db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    goal = db.Column(db.String(255), nullable=False)
    topic = db.Column(db.Text, nullable=True)
    due_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    sort_order = db.Column(db.Integer, default=0)

    # Relationship to Week
    week_id = db.Column(db.Integer, db.ForeignKey("weeks.id"), nullable=True)
    week = db.relationship("Week", back_populates="tasks")

    # Status column (stores Enum)
    status = db.Column(
        db.Enum(StatusEnum, name="status_enum"),
        nullable=False,
        default=StatusEnum.NOT_STARTED,
    )

    def __repr__(self):
        return f"<Task {self.id}: {self.goal} [{self.status.value}]>"

    # Optional: convenience method to display date nicely
    def formatted_due_date(self):
        return self.due_date.strftime("%b %d, %Y") if self.due_date else "N/A"

    # Optional: convenience method for progress logic
    def is_complete(self):
        return self.status == StatusEnum.COMPLETE
