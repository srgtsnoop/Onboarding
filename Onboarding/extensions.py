# Onboarding/extensions.py
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()  # single shared instance for the whole app
