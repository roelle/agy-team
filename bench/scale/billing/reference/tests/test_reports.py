from decimal import Decimal

from billing.reports import refund_rate, top_customers


def test_top_customers_are_ordered_highest_first():
    ranked = top_customers(3)
    assert len(ranked) == 3
    assert ranked[0][1] >= ranked[1][1] >= ranked[2][1]


def test_refund_rate_is_a_small_percentage():
    rate = refund_rate()
    assert Decimal("0") < rate < Decimal("5")
