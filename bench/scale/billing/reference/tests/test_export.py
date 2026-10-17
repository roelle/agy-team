from decimal import Decimal

from billing.export import from_csv, parse_amount, revenue, to_csv
from billing.invoice import Invoice


def invoices():
    a = Invoice(3, "acme", "OR", exempt=True)
    a.add_line("x", 1, "10.00")
    b = Invoice(5, "zen", "OR", exempt=True)
    b.add_line("y", 2, "1.50")
    return [b, a]


def test_round_trip():
    rows = from_csv(to_csv(invoices()))
    assert [r["invoice"] for r in rows] == ["INV-3", "INV-5"]
    assert rows[0]["total"] == Decimal("10.00")


def test_revenue():
    assert revenue(to_csv(invoices())) == Decimal("13.00")


def test_parse_amount_with_separator():
    assert parse_amount("1,234.50") == Decimal("1234.50")
