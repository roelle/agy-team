"""Write scheduling/reference/data/meetings.csv: a year of room bookings.

Deterministic, about 7,000 rows, roughly one in ten cancelled.
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent / "reference" / "data" / "meetings.csv"


def main():
    rng = random.Random(20261019)
    rooms = ["atlas", "borealis", "booth-1", "booth-2"]
    start = date(2025, 1, 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "room", "start", "end", "status"])
        for _ in range(7000):
            d = start + timedelta(days=rng.randrange(365))
            s = rng.randrange(9 * 60, 16 * 60, 15)
            e = s + rng.choice([15, 30, 45, 60, 90])
            status = "cancelled" if rng.random() < 0.1 else "held"
            w.writerow([d.isoformat(), rng.choice(rooms), s, e, status])


if __name__ == "__main__":
    main()
