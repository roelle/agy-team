from decimal import Decimal

import pytest

from billing.money import allocate, format_money, round_cents, to_money


def test_to_money_rounds_half_up():
    assert to_money("1.005") == Decimal("1.01")
    assert to_money("2") == Decimal("2.00")


def test_to_money_refuses_floats():
    with pytest.raises(TypeError):
        to_money(1.5)


def test_round_cents_keeps_exact_values():
    assert round_cents(Decimal("3.10")) == Decimal("3.10")


def test_allocate_even_split():
    assert allocate("9.00", 3) == [Decimal("3.00")] * 3


def test_allocate_sums_to_total():
    assert sum(allocate("100.00", 4)) == Decimal("100.00")


def test_format_money():
    assert format_money(Decimal("1234.5")) == "USD 1,234.50"
    assert format_money(Decimal("-3"), "EUR") == "EUR -3.00"
