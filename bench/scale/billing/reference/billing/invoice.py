"""Invoices: lines, totals, tax, and the number they are filed under."""
from dataclasses import dataclass, field
from decimal import Decimal

from .discounts import line_price
from .money import round_cents, to_money
from .tax import compute_tax


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
    """An invoice for one customer in one region.

    Lines are added with add_line; tags are free-form labels a line carries
    into the export (for example "hardware" or "promo").
    """

    def __init__(self, number: int, customer: str, region: str, exempt: bool = False):
        self.number = number
        self.customer = customer
        self.region = region
        self.exempt = exempt
        self.lines: list[Line] = []

    def add_line(self, item: str, quantity: int, unit_price, coupon=None,
                 tags=None) -> Line:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        line = Line(item, quantity, to_money(unit_price), coupon,
                    list(tags) if tags else [])
        self.lines.append(line)
        return line

    def tag(self, line_index: int, tag: str) -> None:
        """Add a tag to one line."""
        self.lines[line_index].tags.append(tag)

    @property
    def subtotal(self) -> Decimal:
        return round_cents(sum((l.amount for l in self.lines), Decimal("0")))

    @property
    def tax(self) -> Decimal:
        return compute_tax(self.subtotal, self.region, self.exempt)

    @property
    def total(self) -> Decimal:
        return self.subtotal + self.tax

    @property
    def label(self) -> str:
        """'INV-123'. Not zero-padded: the numbers predate this system and the
        printed labels must match the old paper ones."""
        return f"INV-{self.number}"
