"""Stock on hand and reservations, per SKU.

available = on hand - reserved, cached per SKU because the storefront asks
for it on every page view; every change to a SKU's stock or reservations
must drop that SKU's cached value.
"""


class Stock:
    def __init__(self):
        self._on_hand: dict[str, int] = {}
        self._reserved: dict[str, int] = {}
        self._available: dict[str, int] = {}

    def receive(self, sku: str, qty: int) -> None:
        if qty <= 0:
            raise ValueError("receive a positive quantity")
        self._on_hand[sku] = self._on_hand.get(sku, 0) + qty
        self._available.pop(sku, None)

    def reserve(self, sku: str, qty: int) -> None:
        if qty > self.available(sku):
            raise ValueError(f"only {self.available(sku)} of {sku} available")
        self._reserved[sku] = self._reserved.get(sku, 0) + qty
        self._available.pop(sku, None)

    def release(self, sku: str, qty: int) -> None:
        """Give back part of a reservation (an order line was cancelled)."""
        held = self._reserved.get(sku, 0)
        if qty > held:
            raise ValueError(f"only {held} of {sku} reserved")
        self._reserved[sku] = held - qty
        self._available.pop(sku, None)

    def ship(self, sku: str, qty: int) -> None:
        """Ship reserved units: they leave both on-hand and reserved."""
        self.release(sku, qty)
        self._on_hand[sku] -= qty
        self._available.pop(sku, None)

    def on_hand(self, sku: str) -> int:
        return self._on_hand.get(sku, 0)

    def available(self, sku: str) -> int:
        if sku not in self._available:
            self._available[sku] = self.on_hand(sku) - self._reserved.get(sku, 0)
        return self._available[sku]
