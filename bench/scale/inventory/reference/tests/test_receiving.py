import pytest

from inventory.receiving import parse_asn, totals


def test_parse_and_total():
    asn = "# supplier 7\nSKU-1, 12\nSKU-2, 5, LOT9\n\nSKU-1, 3\n"
    assert totals(parse_asn(asn)) == {"SKU-1": 15, "SKU-2": 5}


def test_bad_line_names_its_number():
    with pytest.raises(ValueError, match="line 2"):
        parse_asn("SKU-1, 1\nSKU-2\n")
