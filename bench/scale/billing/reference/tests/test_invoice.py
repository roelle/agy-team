from decimal import Decimal

from billing.invoice import Invoice


def make():
    inv = Invoice(7, "acme", "CA")
    inv.add_line("widget", 2, "10.00")
    inv.add_line("gadget", 1, "5.00")
    return inv


def test_totals():
    inv = make()
    assert inv.subtotal == Decimal("25.00")
    assert inv.tax == Decimal("2.19")
    assert inv.total == Decimal("27.19")


def test_label():
    assert make().label == "INV-7"


def test_tags_belong_to_one_line():
    inv = make()
    inv.tag(0, "hardware")
    assert inv.lines[0].tags == ["hardware"]
    assert inv.lines[1].tags == []
