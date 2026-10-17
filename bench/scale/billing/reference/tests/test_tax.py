from decimal import Decimal

from billing.tax import compute_tax, effective_rate, tax_breakdown


def test_california_rate():
    assert effective_rate("CA") == Decimal("8.75")


def test_compute_tax():
    assert compute_tax("100.00", "CA") == Decimal("8.75")
    assert compute_tax("100.00", "TX") == Decimal("8.25")


def test_exempt_customer_pays_nothing():
    assert compute_tax("100.00", "NY", exempt=True) == Decimal("0.00")


def test_breakdown_has_one_entry_per_component():
    assert len(tax_breakdown("10.00", "CA")) == 3
