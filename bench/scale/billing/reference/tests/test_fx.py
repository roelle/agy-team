from decimal import Decimal

from billing.fx import Converter, table_source


def test_convert_and_cache():
    c = Converter(table_source({("USD", "EUR"): "0.90"}))
    assert c.convert("10.00", "USD", "EUR") == Decimal("9.00")
    c.convert("20.00", "USD", "EUR")
    assert c.fetches == 1


def test_same_currency_is_free():
    c = Converter(table_source({}))
    assert c.convert("5.00", "USD", "USD") == Decimal("5.00")
