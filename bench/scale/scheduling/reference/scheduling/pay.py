"""Pay for scheduled shifts: overtime past 40 hours a week at 1.5x.

Amounts are Decimal, rounded half up to the cent, as payroll files them.
"""
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def cents(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def weekly_pay(hours: Decimal, rate: Decimal) -> Decimal:
    hours, rate = Decimal(str(hours)), Decimal(str(rate))
    regular = min(hours, Decimal("40"))
    overtime = max(hours - Decimal("40"), Decimal("0"))
    return cents(regular * rate + overtime * rate * Decimal("1.5"))
