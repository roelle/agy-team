"""Money as Decimal, rounded to the cent the way the finance team books it.

Every amount that leaves this module has been quantized to two places with
ROUND_HALF_UP: 2.675 is 2.68, and -2.675 is -2.68. Floats never enter a total.
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


def to_money(value) -> Decimal:
    """Parse an int, str or Decimal into a cent-rounded Decimal.

    Floats are refused: Decimal(2.675) is 2.67499999... and would round the
    wrong way before anyone looked.
    """
    if isinstance(value, float):
        raise TypeError("pass money as str, int or Decimal, never float")
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError) as e:
        raise ValueError(f"not an amount: {value!r}") from e
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def round_cents(amount: Decimal) -> Decimal:
    """Round a Decimal (for example a computed tax) to the cent, half up."""
    return Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)


def allocate(total, parts: int) -> list[Decimal]:
    """Split `total` into `parts` amounts that sum to exactly `total`.

    Every part is total/parts rounded down to the cent; the cents left over
    go one each to the first parts. allocate("10.00", 3) is
    [3.34, 3.33, 3.33].
    """
    if parts <= 0:
        raise ValueError("parts must be positive")
    total = to_money(total)
    cents = int(total * 100)
    base, extra = divmod(cents, parts)
    out = []
    for i in range(parts):
        share = base + (1 if i < extra else 0)
        out.append((Decimal(share) / 100).quantize(CENT))
    return out


def format_money(amount: Decimal, currency: str = "USD") -> str:
    """'USD 1,234.50' -- thousands separators, always two decimals."""
    amount = round_cents(amount)
    sign = "-" if amount < 0 else ""
    return f"{currency} {sign}{abs(amount):,.2f}"
