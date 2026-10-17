from decimal import Decimal

from scheduling.pay import weekly_pay


def test_regular_week():
    assert weekly_pay(Decimal("38"), Decimal("20.00")) == Decimal("760.00")


def test_overtime():
    assert weekly_pay(Decimal("45"), Decimal("20.00")) == Decimal("950.00")
