from app import app
from models import Week, db
import os

with app.app_context():
    print("DB exists:", os.path.exists("db.sqlite3"))
    print("Weeks count:", db.session.query(Week).count())
