"""Coupons and volume tiers.

A coupon is either a percentage ("10%") or a fixed amount ("5.00"). Volume
tiers give a percentage off once the quantity reaches the tier's threshold:
an order of exactly 100 units is in the 100-unit tier.
"""
from decimal import Decimal

from .money import round_cents, to_money

TIERS = [  # (minimum quantity, percent off), ascending
    (10, Decimal("2")),
    (50, Decimal("5")),
    (100, Decimal("8")),
    (500, Decimal("12")),
]


def tier_percent(quantity: int) -> Decimal:
    """Percent off for `quantity` units: the highest tier whose threshold
    the quantity has reached."""
    percent = Decimal("0")
    for minimum, off in TIERS:
        if quantity >= minimum:
            percent = off
    return percent


def apply_coupon(amount, coupon: str | None) -> Decimal:
    """`amount` after a coupon; never below zero."""
    amount = to_money(amount)
    if not coupon:
        return amount
    coupon = coupon.strip()
    if coupon.endswith("%"):
        off = round_cents(amount * Decimal(coupon[:-1]) / 100)
    else:
        off = to_money(coupon)
    return max(amount - off, Decimal("0.00"))


def line_price(unit_price, quantity: int, coupon: str | None = None) -> Decimal:
    """Price of a line: unit x quantity, less the volume tier, then the
    coupon. The tier is applied first so a fixed coupon is worth the same on
    any size of order."""
    gross = to_money(unit_price) * quantity
    after_tier = round_cents(gross * (100 - tier_percent(quantity)) / 100)
    return apply_coupon(after_tier, coupon)
