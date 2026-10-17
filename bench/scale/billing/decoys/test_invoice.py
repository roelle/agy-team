from dataclasses import dataclass, field
from decimal import Decimal

from billing.discounts import line_price
from billing.money import round_cents, to_money
from billing.tax import compute_tax


# Local stand-ins so these tests run without the rest of the system.
@dataclass
class Line:
    item: str
    quantity: int
    unit_price: Decimal
    coupon: str | None = None
    tags: list[str] = field(default_factory=list)

    @property
    def amount(self) -> Decimal:
        return line_price(self.unit_price, self.quantity, self.coupon)


class Invoice:
    def __init__(self, number, customer, region, exempt=False):
        self.number, self.customer, self.region, self.exempt = number, customer, region, exempt
        self.lines = []

    def add_line(self, item, quantity, unit_price, coupon=None, tags=None):
        line = Line(item, quantity, to_money(unit_price), coupon, list(tags) if tags else [])
        self.lines.append(line)
        return line

    def tag(self, i, tag):
        self.lines[i].tags.append(tag)

    @property
    def subtotal(self):
        return round_cents(sum((l.amount for l in self.lines), Decimal("0")))

    @property
    def tax(self):
        return compute_tax(self.subtotal, self.region, self.exempt)

    @property
    def total(self):
        return self.subtotal + self.tax

    @property
    def label(self):
        return f"INV-{self.number}"


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
