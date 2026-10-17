from decimal import Decimal

import pytest

from inventory.valuation import FifoLedger


def ledger():
    f = FifoLedger()
    f.receive(10, "2.00")
    f.receive(10, "3.00")
    return f


def test_issue_takes_oldest_first():
    f = ledger()
    assert f.issue(12) == Decimal("26.00")
    assert f.on_hand() == 8


def test_value_of_what_is_left():
    f = ledger()
    f.issue(5)
    assert f.value() == Decimal("40.00")


def test_cannot_overissue():
    with pytest.raises(ValueError):
        ledger().issue(21)


def test_average_cost():
    assert ledger().average_cost() == Decimal("2.50")
