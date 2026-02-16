from app import app
from Onboarding.models import User
from Onboarding.extensions import db
from sqlalchemy.orm import selectinload
import os

if os.path.exists("db.sqlite3"):
    print(f"db.sqlite3 size: {os.path.getsize('db.sqlite3')}")
else:
    print("db.sqlite3 does NOT exist")

try:
    with app.app_context():
        count = db.session.query(User).count()
        print(f"Users count: {count}")
except Exception as e:
    print(f"Error querying DB: {e}")
