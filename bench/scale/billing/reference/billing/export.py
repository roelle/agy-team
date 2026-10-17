"""CSV export and import of invoice summaries.

Rows are ordered by invoice number. Amounts are written without currency
symbols and read back exactly; a row that cannot be read is an error, not a
zero -- a silently zeroed invoice is how revenue goes missing.
"""
import csv
import io
from decimal import Decimal, InvalidOperation

from .money import to_money

FIELDS = ["invoice", "customer", "region", "subtotal", "tax", "total"]


def to_rows(invoices) -> list[dict]:
    rows = [{"invoice": inv.label, "customer": inv.customer, "region": inv.region,
             "subtotal": str(inv.subtotal), "tax": str(inv.tax),
             "total": str(inv.total), "_n": inv.number} for inv in invoices]
    rows.sort(key=lambda r: r["_n"])
    for r in rows:
        del r["_n"]
    return rows


def to_csv(invoices) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(to_rows(invoices))
    return buf.getvalue()


def parse_amount(text: str) -> Decimal:
    """'1,234.50' or '1234.50' -> Decimal('1234.50'). Raises on anything else."""
    cleaned = (text or "").strip().replace(",", "")
    try:
        return to_money(cleaned)
    except (ValueError, InvalidOperation) as e:
        raise ValueError(f"unreadable amount {text!r}") from e


def from_csv(text: str) -> list[dict]:
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        out.append({**row, **{k: parse_amount(row[k]) for k in ("subtotal", "tax", "total")}})
    return out


def revenue(text: str) -> Decimal:
    """Sum of the totals in an exported CSV."""
    return sum((r["total"] for r in from_csv(text)), Decimal("0.00"))
