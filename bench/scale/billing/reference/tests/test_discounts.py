from decimal import Decimal

from billing.discounts import apply_coupon, line_price, tier_percent


def test_no_tier_below_ten():
    assert tier_percent(5) == Decimal("0")


def test_mid_tier():
    assert tier_percent(75) == Decimal("5")


def test_percentage_coupon():
    assert apply_coupon("50.00", "10%") == Decimal("45.00")


def test_fixed_coupon_never_negative():
    assert apply_coupon("3.00", "5.00") == Decimal("0.00")


def test_line_price_tier_then_coupon():
    assert line_price("1.00", 20, "1.00") == Decimal("18.60")
