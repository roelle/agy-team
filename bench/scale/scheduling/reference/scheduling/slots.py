"""Bookable slots in a working day.

A slot must fit entirely inside the day: with a day of 09:00-17:00 and
30-minute slots, the last slot starts at 16:30 and ends at 17:00.
"""
from datetime import datetime, timedelta


def day_slots(start: datetime, end: datetime, minutes: int) -> list[datetime]:
    """Start times of every `minutes`-long slot between `start` and `end`."""
    if minutes <= 0:
        raise ValueError("slot length must be positive")
    step = timedelta(minutes=minutes)
    out = []
    t = start
    while t + step <= end:
        out.append(t)
        t += step
    return out


def slot_count(start: datetime, end: datetime, minutes: int) -> int:
    return len(day_slots(start, end, minutes))
