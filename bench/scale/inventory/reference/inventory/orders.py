"""Customer orders and their lines."""
from dataclasses import dataclass, field


@dataclass
class OrderLine:
    sku: str
    qty: int


@dataclass
class Order:
    id: int
    customer: str
    lines: list[OrderLine] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def total_units(self) -> int:
        return sum(l.qty for l in self.lines)


_next_id = [1]


def create_order(customer: str, lines=None, notes=None) -> Order:
    """A new order with its own lines and notes lists."""
    order = Order(_next_id[0], customer,
                  [OrderLine(s, q) for s, q in (lines or [])],
                  list(notes) if notes else [])
    _next_id[0] += 1
    return order


def add_note(order: Order, note: str) -> None:
    order.notes.append(note)
