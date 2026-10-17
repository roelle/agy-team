from decimal import Decimal

import pytest

from billing.ledger import Ledger


def test_balance_sums_entries():
    led = Ledger()
    led.post("acme", "10.00")
    led.post("acme", "-4.00")
    assert led.balance("acme") == Decimal("6.00")


def test_reversal_zeroes_the_balance():
    led = Ledger()
    e = led.post("acme", "10.00")
    assert led.balance("acme") == Decimal("10.00")
    led.reverse(e.id)
    assert led.balance("acme") == Decimal("0.00")


def test_double_reversal_is_refused():
    led = Ledger()
    e = led.post("acme", "1.00")
    led.reverse(e.id)
    with pytest.raises(ValueError):
        led.reverse(e.id)
