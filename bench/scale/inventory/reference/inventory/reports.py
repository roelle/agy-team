"""Movement reports over data/movements.csv.

Each row is a stock movement: a receipt (positive), a shipment (negative) or
an adjustment (either sign: a count that found more, or shrinkage). Net
movement includes everything; shrinkage is the negative adjustments.
"""
import csv
from collections import defaultdict
from pathlib import Path

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data" / "movements.csv"


def _rows(path=None):
    with open(path or DEFAULT_DATA, newline="") as f:
        yield from csv.DictReader(f)


def net_movement(path=None) -> dict[str, int]:
    """{sku: net units moved}, every movement type included."""
    out: dict[str, int] = defaultdict(int)
    for row in _rows(path):
        out[row["sku"]] += int(row["qty"])
    return dict(sorted(out.items()))


def shrinkage(path=None) -> dict[str, int]:
    """{sku: units lost to negative adjustments}, as positive numbers."""
    out: dict[str, int] = defaultdict(int)
    for row in _rows(path):
        q = int(row["qty"])
        if row["kind"] == "adjustment" and q < 0:
            out[row["sku"]] += -q
    return dict(sorted(out.items()))


def busiest(n: int = 5, path=None) -> list[str]:
    """The n SKUs with the most movements of any kind, most first, ties by sku."""
    counts: dict[str, int] = defaultdict(int)
    for row in _rows(path):
        counts[row["sku"]] += 1
    return [s for s, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]
