"""Overdue invoices and how hard to chase them.

Levels: 0 not overdue, 1 reminder (1-14 days late), 2 warning (15-44),
3 final notice (45+). An invoice due today is not late.
"""
from datetime import date

LEVELS = [(45, 3), (15, 2), (1, 1)]


def days_overdue(due: date, today: date) -> int:
    return max((today - due).days, 0)


def level(due: date, today: date) -> int:
    late = days_overdue(due, today)
    for threshold, lvl in LEVELS:
        if late >= threshold:
            return lvl
    return 0


def notices(invoices_due: dict[str, date], today: date) -> dict[int, list[str]]:
    """Group invoice labels by dunning level, levels 1-3 only, labels sorted."""
    out: dict[int, list[str]] = {}
    for label, due in invoices_due.items():
        lvl = level(due, today)
        if lvl:
            out.setdefault(lvl, []).append(label)
    for labels in out.values():
        labels.sort()
    return out


def parse_due(text: str) -> date:
    """'2026-03-31' -> date. A malformed date is re-raised with the input in
    the message; the broad except is deliberate, to add context, not to hide."""
    try:
        y, m, d = (int(p) for p in text.split("-"))
        return date(y, m, d)
    except Exception as e:
        raise ValueError(f"bad due date {text!r}: {e}") from e
