from decimal import Decimal

import pytest

from inventory.units import Converter


def test_cases_to_each():
    assert Converter().convert(2, "case", "each") == Decimal("48.000")


def test_each_to_dozen_rounds_to_three_places():
    assert Converter().convert(5, "each", "dozen") == Decimal("0.417")


def test_unknown_unit_is_an_error():
    with pytest.raises(KeyError):
        Converter().convert(1, "crate", "each")
