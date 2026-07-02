# Onboarding Platform

A Flask + HTMX app for building and tracking employee onboarding plans with
role-based access (User / Manager / Builder / Admin), reusable plan templates,
and DOCX template import.

## Quick start

```bash
pip install -r requirements.txt
python seed.py        # creates db.sqlite3 with demo users and a sample plan
flask --app app run   # http://127.0.0.1:5000
```

Demo users (switch with the "View as" dropdown in the nav bar — your choice
is remembered for the session):

| Email               | Role    |
|---------------------|---------|
| admin@example.com   | admin   |
| manager@example.com | manager |
| user@example.com    | user    |

## Key pages

- `/weeks` – your onboarding plan with overall and per-week progress bars
- `/weeks/<id>` – week detail: add tasks, inline-edit status/notes/due dates (HTMX)
- `/templates` – build reusable onboarding templates (builder/admin)
- `/templates/import` – import a template from a Word (.docx) file
- `/assign` – assign a published template to a new hire (manager/admin)
- `/manager/reports`, `/manager/plans`, `/admin/overview` – role dashboards

## API-style access

`/weeks`, `/weeks/<id>`, `/api/my-plan`, `/api/manager/reports`, and
`/api/admin/overview` honor `X-User-Id` / `X-User-Role` headers and enforce
the access policy in `Onboarding/policy.py` (users see only their own weeks,
managers see their reports' weeks, admins see everything).

## Tests

```bash
pytest test/ test_roles.py
```

## Configuration

- `SECRET_KEY` – set in production (defaults to a dev value)
- Database is SQLite at `./db.sqlite3`; delete it and re-run `seed.py` to reset.

## Notes

This is a development setup (Tailwind/HTMX via CDN, Flask dev server, no real
authentication — the user switcher simulates identity). Before production use,
add a login system, pin frontend assets, and run behind a WSGI server.
