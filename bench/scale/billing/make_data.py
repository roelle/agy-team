"""Write billing/reference/data/invoices.csv: a year of invoice summaries.

Deterministic (fixed seed), about 6,000 rows, a few percent of them refunds
(negative totals). Big on purpose: an agent that reads it whole spends its
context on it; one that samples and computes does not.
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent / "reference" / "data" / "invoices.csv"
CUSTOMERS = [f"cust-{i:03d}" for i in range(1, 121)]
REGIONS = ["CA", "NY", "TX", "OR", "WA"]


def main():
    rng = random.Random(20261017)
    start = date(2025, 1, 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["invoice", "date", "customer", "region", "total"])
        for n in range(1, 6001):
            d = start + timedelta(days=rng.randrange(365))
            cents = rng.randrange(500, 250000)
            if rng.random() < 0.04:
                cents = -rng.randrange(500, 50000)
            w.writerow([f"INV-{n}", d.isoformat(), rng.choice(CUSTOMERS),
                        rng.choice(REGIONS), f"{cents / 100:.2f}"])


if __name__ == "__main__":
    main()
