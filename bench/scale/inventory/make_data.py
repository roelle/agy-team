"""Write inventory/reference/data/movements.csv: a year of stock movements.

Deterministic, about 8,000 rows. Big on purpose; see billing/make_data.py.
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent / "reference" / "data" / "movements.csv"


def main():
    rng = random.Random(20261018)
    skus = [f"SKU-{i:04d}" for i in range(1, 301)]
    start = date(2025, 1, 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "sku", "kind", "qty"])
        for _ in range(8000):
            d = start + timedelta(days=rng.randrange(365))
            r = rng.random()
            if r < 0.45:
                kind, qty = "receipt", rng.randrange(10, 500)
            elif r < 0.92:
                kind, qty = "shipment", -rng.randrange(1, 120)
            else:
                kind, qty = "adjustment", rng.choice([-1, -1, -1, 1]) * rng.randrange(1, 30)
            w.writerow([d.isoformat(), rng.choice(skus), kind, qty])


if __name__ == "__main__":
    main()
