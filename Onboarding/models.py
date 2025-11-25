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


class RoleEnum(str, Enum):
    USER = "user"
    MANAGER = "manager"
    ADMIN = "admin"


# -------------------------------------------------------------------
# MODEL: OnboardingPlan (collection of onboarding weeks)
# -------------------------------------------------------------------
class OnboardingPlan(db.Model):
    __tablename__ = "onboarding_plans"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, default="Onboarding Plan")
    description = db.Column(db.Text, nullable=True)

    weeks = db.relationship(
        "Week",
        back_populates="onboarding_plan",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    users = db.relationship(
        "User",
        back_populates="onboarding_plan",
        lazy="selectin",
    )


# -------------------------------------------------------------------
# MODEL: Week (a collection of tasks)
# -------------------------------------------------------------------
class Week(db.Model):
    __tablename__ = "weeks"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False, default="Week")
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    owner_user_id = db.Column(db.Integer, nullable=True)
    manager_user_id = db.Column(db.Integer, nullable=True)

    onboarding_plan_id = db.Column(
        db.Integer, db.ForeignKey("onboarding_plans.id"), nullable=True
    )
    onboarding_plan = db.relationship("OnboardingPlan", back_populates="weeks")

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
    due_date = db.Column(db.Date, nullable=True)  # <-- add this

    # Store the status as a string matching StatusEnum values
    status = db.Column(
        db.String(32), nullable=False, default=StatusEnum.NOT_STARTED.value
    )

    notes = db.Column(db.Text, nullable=False, default="")
    sort_order = db.Column(db.Integer, nullable=True)

    def __repr__(self):
        return f"<Task {self.id}: {self.goal or self.title} [{self.status}]>"

    def formatted_due_date(self) -> str:
        if not self.due_date:
            return ""
        # Use a portable format, then clean up the leading zero in the day
        return self.due_date.strftime("%b %d, %Y").replace(" 0", " ")

    def is_complete(self):
        return self.status == StatusEnum.COMPLETE.value


# -------------------------------------------------------------------
# MODEL: User
# -------------------------------------------------------------------
class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    full_name = db.Column(db.String(255), nullable=False)

    role = db.Column(db.String(32), nullable=False, default=RoleEnum.USER.value)

    onboarding_plan_id = db.Column(
        db.Integer, db.ForeignKey("onboarding_plans.id"), nullable=True
    )
    onboarding_plan = db.relationship("OnboardingPlan", back_populates="users")

    manager_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    manager = db.relationship(
        "User",
        remote_side="User.id",
        back_populates="direct_reports",
        lazy="joined",
    )

    direct_reports = db.relationship(
        "User",
        back_populates="manager",
        lazy="selectin",
        cascade="all",
    )

    def __repr__(self):
        return f"<User {self.full_name} ({self.role})>"
