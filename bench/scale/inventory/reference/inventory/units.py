"""Unit conversion through a factor table, cached per (from, to) pair.

Quantities are Decimal and rounded to three places half up, the precision
the warehouse scales report. Unknown units are an error, never a guess.
"""
from decimal import ROUND_HALF_UP, Decimal

FACTORS = {  # units per one base unit (the base is "each")
    "each": Decimal("1"),
    "pair": Decimal("0.5"),
    "dozen": Decimal("1") / Decimal("12"),
    "case": Decimal("1") / Decimal("24"),
    "pallet": Decimal("1") / Decimal("960"),
}
MILLI = Decimal("0.001")


class Converter:
    def __init__(self, factors=None):
        self._factors = dict(factors or FACTORS)
        self._cache: dict[tuple[str, str], Decimal] = {}

    def factor(self, from_unit: str, to_unit: str) -> Decimal:
        """How many `to_unit` make one `from_unit`."""
        for u in (from_unit, to_unit):
            if u not in self._factors:
                raise KeyError(f"unknown unit {u!r}")
        key = (from_unit, to_unit)
        if key not in self._cache:
            self._cache[key] = self._factors[to_unit] / self._factors[from_unit]
        return self._cache[key]

    def convert(self, qty, from_unit: str, to_unit: str) -> Decimal:
        value = Decimal(str(qty)) * self.factor(from_unit, to_unit)
        return value.quantize(MILLI, rounding=ROUND_HALF_UP)
