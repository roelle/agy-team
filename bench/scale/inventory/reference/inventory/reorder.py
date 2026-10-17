"""When to reorder, and how much safety stock to hold.

A SKU is reordered when what is available has fallen to its reorder point or
below: at exactly the point, the order goes out.
"""
import math


def needs_reorder(available: int, reorder_point: int) -> bool:
    return available <= reorder_point


def safety_stock(daily_demand: float, lead_days: int, cover_days: int = 2) -> int:
    """Units to hold back: demand over the lead time plus `cover_days` extra,
    rounded UP to a whole unit -- holding a fraction short is a stockout."""
    return math.ceil(daily_demand * (lead_days + cover_days))


def reorder_quantity(available: int, reorder_point: int, order_up_to: int) -> int:
    """Units to order to bring a SKU back up to `order_up_to`, or 0."""
    if not needs_reorder(available, reorder_point):
        return 0
    return max(order_up_to - available, 0)
