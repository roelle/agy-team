"""Shelf life and expiry of received batches.

SHELF_LIFE gives days per product family. 0 means the family does not
expire (hardware, fasteners); a family missing from the table gets
DEFAULT_SHELF_LIFE until someone configures it.
"""
from datetime import date, timedelta

SHELF_LIFE = {"dairy": 14, "produce": 7, "frozen": 180, "hardware": 0, "fasteners": 0}
DEFAULT_SHELF_LIFE = 30


def shelf_life(family: str) -> int:
    if family in SHELF_LIFE:
        return SHELF_LIFE[family]
    return DEFAULT_SHELF_LIFE


def expires_on(received: date, family: str) -> date | None:
    """The last sellable day, or None for a family that does not expire. A
    14-day shelf life received on the 1st is sellable through the 14th."""
    days = shelf_life(family)
    if days == 0:
        return None
    return received + timedelta(days=days - 1)


def expiring(batches: list[dict], today: date, within_days: int) -> list[str]:
    """Batch ids whose last sellable day falls within the next `within_days`
    days, today included, soonest first."""
    horizon = today + timedelta(days=within_days)
    due = []
    for b in batches:
        last = expires_on(b["received"], b["family"])
        if last is not None and today <= last <= horizon:
            due.append((last, b["id"]))
    return [bid for _, bid in sorted(due)]
