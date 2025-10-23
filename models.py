from __future__ import annotations
from datetime import date
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Enum as SAEnum
from enum import Enum


db = SQLAlchemy()


class StatusEnum(str, Enum):
    TO_START = "To Start"
    IN_PROGRESS = "In Progress"
    BLOCKED = "Blocked"
    DONE = "Done"


class Week(db.Model):
    __tablename__ = "weeks"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    tasks = db.relationship("Task", backref="week", order_by="Task.sort_order")


class Task(db.Model):
    __tablename__ = "tasks"
    id = db.Column(db.Integer, primary_key=True)
    week_id = db.Column(db.Integer, db.ForeignKey("weeks.id"), nullable=False)


goal = db.Column(db.String(300), nullable=False) # e.g., "Job shadow Joel (his desk)"
topic = db.Column(db.Text, nullable=True) # bullet list allowed (\n separated)


due_date = db.Column(db.Date, nullable=True)
notes = db.Column(db.Text, nullable=True)


status = db.Column(SAEnum(StatusEnum), default=StatusEnum.TO_START, nullable=False)
sort_order = db.Column(db.Integer, default=0)


def status_options(self):
    return [s.value for s in StatusEnum]