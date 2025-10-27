import os, inspect
from app import app
from models import db, Week, Task

print("app.py path:", inspect.getsourcefile(app.__class__))
print("cwd:", os.getcwd())

with app.app_context():
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    db_path = uri.replace("sqlite:///", "")
    print("SQLALCHEMY_DATABASE_URI:", uri)
    print("Resolved DB path:", db_path)
    print("DB exists:", os.path.exists(db_path))

    # show table names
    engine = db.get_engine()
    insp = db.inspect(engine)
    print("Tables:", insp.get_table_names())

    try:
        print("Weeks count:", db.session.query(Week).count())
        print("Tasks count:", db.session.query(Task).count())
    except Exception as e:
        print("Query error:", repr(e))
