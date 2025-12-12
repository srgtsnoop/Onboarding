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
    BUILDER = "builder"
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


# -------------------------------------------------------------------
# Additional Enums for Templates
# -------------------------------------------------------------------
class TemplateStatusEnum(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


class ResponsiblePartyEnum(str, Enum):
    NEW_HIRE = "New Hire"
    MANAGER = "Manager"
    OTHER = "Other"


class DueTypeEnum(str, Enum):
    DAYS_FROM_START = "days_from_start"
    DAY_WITHIN_SECTION = "day_within_section"


# -------------------------------------------------------------------
# TEMPLATE LAYER
#   - OnboardingTemplate
#   - TemplateSection (Week/Phase)
#   - TemplateTask
# -------------------------------------------------------------------
class OnboardingTemplate(db.Model):
    """
    Reusable onboarding blueprint, e.g. 'New Engineer – 90 Day Plan'.
    """

    __tablename__ = "onboarding_templates"

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)

    status = db.Column(
        db.String(32),
        nullable=False,
        default=TemplateStatusEnum.DRAFT.value,
    )

    # Classification / discovery
    target_role = db.Column(db.String(120), nullable=True)  # e.g. 'Engineer'
    department = db.Column(db.String(120), nullable=True)
    location = db.Column(db.String(120), nullable=True)

    # Optional duration hint
    estimated_duration_days = db.Column(db.Integer, nullable=True)

    # Lightweight version tracking
    version = db.Column(db.String(50), nullable=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by = db.relationship("User", backref="created_templates")

    created_at = db.Column(db.DateTime, nullable=False, default=date.today)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=date.today,
        onupdate=date.today,
    )

    sections = db.relationship(
        "TemplateSection",
        back_populates="template",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="TemplateSection.order_index.asc()",
    )

    def __repr__(self) -> str:
        return f"<OnboardingTemplate {self.id} {self.name!r} ({self.status})>"


class TemplateSection(db.Model):
    """
    A week/phase inside a template, e.g. 'Week 1 – Orientation'.
    """

    __tablename__ = "template_sections"

    id = db.Column(db.Integer, primary_key=True)

    template_id = db.Column(
        db.Integer,
        db.ForeignKey("onboarding_templates.id"),
        nullable=False,
    )
    template = db.relationship("OnboardingTemplate", back_populates="sections")

    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)

    # Order within the template (1-based)
    order_index = db.Column(db.Integer, nullable=False, default=1)

    # Relative offset in days from plan start (Week 1 = 0, Week 2 = 7, etc.)
    offset_days = db.Column(db.Integer, nullable=True)

    tasks = db.relationship(
        "TemplateTask",
        back_populates="section",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="TemplateTask.order_index.asc()",
    )

    def __repr__(self) -> str:
        return (
            f"<TemplateSection {self.id} {self.title!r} template_id={self.template_id}>"
        )


class TemplateTask(db.Model):
    """
    A generic task definition within a template section.
    This will later materialize into real Task rows when a plan is created.
    """

    __tablename__ = "template_tasks"

    id = db.Column(db.Integer, primary_key=True)

    section_id = db.Column(
        db.Integer,
        db.ForeignKey("template_sections.id"),
        nullable=False,
    )
    section = db.relationship("TemplateSection", back_populates="tasks")

    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)

    responsible_party = db.Column(
        db.String(32),
        nullable=False,
        default=ResponsiblePartyEnum.NEW_HIRE.value,
    )

    due_type = db.Column(
        db.String(32),
        nullable=False,
        default=DueTypeEnum.DAYS_FROM_START.value,
    )

    # If due_type == DAYS_FROM_START → number of days after plan start
    offset_days = db.Column(db.Integer, nullable=True)

    # If due_type == DAY_WITHIN_SECTION → day index within that section (1-based)
    section_day = db.Column(db.Integer, nullable=True)

    category = db.Column(db.String(120), nullable=True)  # e.g. 'Compliance'
    is_required = db.Column(db.Boolean, nullable=False, default=True)
    default_estimated_minutes = db.Column(db.Integer, nullable=True)

    order_index = db.Column(db.Integer, nullable=False, default=1)

    def __repr__(self) -> str:
        return f"<TemplateTask {self.id} {self.title!r} section_id={self.section_id}>"
