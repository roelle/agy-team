"""Revenue reports over an exported invoice history (data/invoices.csv).

Refunds are rows with a negative total. They are revenue going out, and every
report here nets them: a month with 10,000 billed and 500 refunded made 9,500.
"""
import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .money import to_money

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data" / "invoices.csv"


def _rows(path=None):
    with open(path or DEFAULT_DATA, newline="") as f:
        yield from csv.DictReader(f)


def monthly_revenue(path=None) -> dict[str, Decimal]:
    """{'2025-01': Decimal(...), ...}: net revenue per month, refunds included."""
    out: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for row in _rows(path):
        total = to_money(row["total"])
        out[row["date"][:7]] += total
    return dict(sorted(out.items()))


def top_customers(n: int = 5, path=None) -> list[tuple[str, Decimal]]:
    """The n customers with the highest net revenue, highest first. Ties are
    broken by customer id so the order is stable."""
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for row in _rows(path):
        totals[row["customer"]] += to_money(row["total"])
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:n]


def refund_rate(path=None) -> Decimal:
    """Refunded amount as a percentage of billed amount, to two places."""
    billed = refunded = Decimal("0")
    for row in _rows(path):
        total = to_money(row["total"])
        if total < 0:
            refunded += -total
        else:
            billed += total
    if not billed:
        return Decimal("0.00")
    return (refunded * 100 / billed).quantize(Decimal("0.01"))
