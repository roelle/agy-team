"""Sales tax by region.

Rates are percentages of the pre-tax amount. A region may list several
components (state, county, district); they are summed, not compounded, as the
rules for every region here require. An exempt customer pays none.
"""
from decimal import Decimal

from .money import round_cents, to_money

RATES = {
    "CA": [Decimal("7.25"), Decimal("1.00"), Decimal("0.50")],
    "NY": [Decimal("4.00"), Decimal("4.875")],
    "TX": [Decimal("6.25"), Decimal("2.00")],
    "OR": [],
    "WA": [Decimal("6.50"), Decimal("3.60")],
}

DEFAULT_RATE = Decimal("8.00")


def effective_rate(region: str) -> Decimal:
    """The combined percentage for a region: the sum of its components.

    A region not in the table is charged DEFAULT_RATE, the conservative rate
    the tax team asked for until the region is configured.
    """
    if region not in RATES:
        return DEFAULT_RATE
    combined = sum(RATES[region], Decimal("0"))
    return combined


def compute_tax(amount, region: str, exempt: bool = False) -> Decimal:
    """Tax owed on `amount` in `region`, rounded half up to the cent."""
    if exempt:
        return Decimal("0.00")
    amount = to_money(amount)
    return round_cents(amount * effective_rate(region) / 100)


def tax_breakdown(amount, region: str) -> dict[str, Decimal]:
    """Each component's share, keyed 'component_<n>'. Shares are rounded
    individually, so they can differ from compute_tax by a cent; that is the
    regulator's own method and is intended."""
    amount = to_money(amount)
    return {f"component_{i}": round_cents(amount * rate / 100)
            for i, rate in enumerate(RATES.get(region, [DEFAULT_RATE]))}
