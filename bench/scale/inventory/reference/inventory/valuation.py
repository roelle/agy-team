"""FIFO valuation: stock is costed in the order it was received.

Each receipt is a layer (qty, unit_cost). Issuing stock consumes the oldest
layers first. Values are Decimal cents, rounded half up.
"""
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def cents(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


class FifoLedger:
    def __init__(self):
        self.layers: list[list] = []          # [qty, unit_cost]

    def receive(self, qty: int, unit_cost) -> None:
        self.layers.append([qty, Decimal(str(unit_cost))])

    def on_hand(self) -> int:
        return sum(q for q, _ in self.layers)

    def value(self) -> Decimal:
        return cents(sum((q * c for q, c in self.layers), Decimal("0")))

    def issue(self, qty: int) -> Decimal:
        """Remove `qty` units, oldest first; return their cost."""
        if qty > self.on_hand():
            raise ValueError("issuing more than is on hand")
        cost = Decimal("0")
        remaining = qty
        while remaining:
            layer = self.layers[0]
            take = min(layer[0], remaining)
            cost += take * layer[1]
            layer[0] -= take
            remaining -= take
            if layer[0] == 0:
                self.layers.pop(0)
        return cents(cost)

    def average_cost(self) -> Decimal:
        """Weighted average unit cost of what is on hand, to the cent."""
        units = self.on_hand()
        if not units:
            return Decimal("0.00")
        return cents(sum((q * c for q, c in self.layers), Decimal("0")) / units)
