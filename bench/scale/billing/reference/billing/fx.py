"""Currency conversion through a rate source, with a per-pair cache.

The rate source is slow (it is a network service in production), so each
(from, to) pair is fetched once per Converter.
"""
from decimal import Decimal

from .money import round_cents, to_money


class Converter:
    def __init__(self, source):
        """`source(from_ccy, to_ccy) -> Decimal` gives units of `to` per `from`."""
        self._source = source
        self._cache: dict[tuple[str, str], Decimal] = {}
        self.fetches = 0

    def rate(self, from_ccy: str, to_ccy: str) -> Decimal:
        if from_ccy == to_ccy:
            return Decimal("1")
        key = (from_ccy, to_ccy)
        if key not in self._cache:
            self.fetches += 1
            self._cache[key] = Decimal(self._source(from_ccy, to_ccy))
        return self._cache[key]

    def convert(self, amount, from_ccy: str, to_ccy: str) -> Decimal:
        return round_cents(to_money(amount) * self.rate(from_ccy, to_ccy))


def table_source(table: dict[tuple[str, str], str]):
    """A rate source over a fixed table, for tests and offline use."""
    def source(a, b):
        if (a, b) in table:
            return Decimal(table[(a, b)])
        if (b, a) in table:
            return 1 / Decimal(table[(b, a)])
        raise KeyError(f"no rate for {a}->{b}")
    return source
