"""Date parsing helpers for user-entered due dates."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

RELATIVE_RE = re.compile(r"^([+-])\s*(\d{1,3})$")


def parse_due_date(text: str | None) -> date | None:
    """
    Parse a user-supplied date string. Accepts:
      - None / empty string -> None
      - "today", "tomorrow"
      - "+N" or "-N" (relative days)
      - "YYYY-MM-DD", "MM/DD/YY", "MM/DD/YYYY"
    """
    if not text:
        return None
    s = text.strip()
    if not s:
        return None

    today = date.today()
    lower = s.lower()

    if lower == "today":
        return today
    if lower == "tomorrow":
        return today + timedelta(days=1)

    m = RELATIVE_RE.match(s)
    if m:
        sign, num = m.groups()
        delta = int(num)
        return today + timedelta(days=delta if sign == "+" else -delta)

    for fmt in ("%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    raise ValueError(f"Could not parse due date: {s!r}")


def parse_due_date_strict(s: str | None) -> date | None:
    """Accept YYYY-MM-DD (browser date), MM/DD/YY, MM/DD/YYYY; else None."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None
