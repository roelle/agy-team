import pytest

from inventory.stock import Stock


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
