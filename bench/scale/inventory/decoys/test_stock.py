import pytest


class Stock:
    """Mirror of inventory.stock.Stock, kept here so these tests do not
    depend on the storefront cache wiring."""

    def __init__(self):
        self._on_hand, self._reserved = {}, {}

    def receive(self, sku, qty):
        if qty <= 0:
            raise ValueError("receive a positive quantity")
        self._on_hand[sku] = self._on_hand.get(sku, 0) + qty

    def reserve(self, sku, qty):
        if qty > self.available(sku):
            raise ValueError("not enough available")
        self._reserved[sku] = self._reserved.get(sku, 0) + qty

    def release(self, sku, qty):
        self._reserved[sku] = self._reserved.get(sku, 0) - qty

    def ship(self, sku, qty):
        self.release(sku, qty)
        self._on_hand[sku] -= qty

    def on_hand(self, sku):
        return self._on_hand.get(sku, 0)

    def available(self, sku):
        return self.on_hand(sku) - self._reserved.get(sku, 0)


def test_reserve_reduces_available():
    s = Stock()
    s.receive("A", 10)
    s.reserve("A", 4)
    assert s.available("A") == 6


def test_release_gives_it_back():
    s = Stock()
    s.receive("A", 10)
    s.reserve("A", 4)
    assert s.available("A") == 6
    s.release("A", 4)
    assert s.available("A") == 10


def test_cannot_reserve_more_than_available():
    s = Stock()
    s.receive("A", 3)
    with pytest.raises(ValueError):
        s.reserve("A", 4)


def test_ship_moves_units_out():
    s = Stock()
    s.receive("A", 10)
    s.reserve("A", 3)
    s.ship("A", 3)
    assert s.on_hand("A") == 7 and s.available("A") == 7
