"""Recurring meetings.

A weekly series runs from its first date to its `until` date, both included:
a series of Mondays until a Monday includes that last Monday.
"""
from datetime import date, timedelta


def weekly(first: date, until: date, every_weeks: int = 1) -> list[date]:
    if every_weeks <= 0:
        raise ValueError("every_weeks must be positive")
    step = timedelta(weeks=every_weeks)
    out, d = [], first
    while d <= until:
        out.append(d)
        d += step
    return out


def skip_dates(dates: list[date], skips: set[date]) -> list[date]:
    """The series without the cancelled occurrences."""
    return [d for d in dates if d not in skips]
