"""Room-use reports over data/meetings.csv.

Each row is a meeting: room, date, start and end minute, status. Cancelled
meetings did not use the room and are excluded from every figure here.
"""
import csv
from collections import defaultdict
from pathlib import Path

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data" / "meetings.csv"
DAY_MINUTES = 8 * 60


def _rows(path=None):
    with open(path or DEFAULT_DATA, newline="") as f:
        yield from csv.DictReader(f)


def minutes_used(path=None) -> dict[str, int]:
    """{room: minutes booked}, cancelled meetings excluded."""
    out: dict[str, int] = defaultdict(int)
    for row in _rows(path):
        if row["status"] == "cancelled":
            continue
        out[row["room"]] += int(row["end"]) - int(row["start"])
    return dict(sorted(out.items()))


def busiest_days(room: str, n: int = 3, path=None) -> list[str]:
    """The n dates with the most meetings in `room`, cancelled excluded."""
    counts: dict[str, int] = defaultdict(int)
    for row in _rows(path):
        if row["room"] == room and row["status"] != "cancelled":
            counts[row["date"]] += 1
    return [d for d, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]
