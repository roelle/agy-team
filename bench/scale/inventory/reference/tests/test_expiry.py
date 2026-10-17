from datetime import date

from inventory.expiry import expires_on, expiring


def test_dairy_is_sellable_through_day_fourteen():
    assert expires_on(date(2026, 3, 1), "dairy") == date(2026, 3, 14)


def test_expiring_soonest_first():
    batches = [
        {"id": "b2", "received": date(2026, 3, 3), "family": "produce"},
        {"id": "b1", "received": date(2026, 3, 1), "family": "produce"},
        {"id": "b3", "received": date(2026, 3, 1), "family": "frozen"},
    ]
    assert expiring(batches, date(2026, 3, 5), 5) == ["b1", "b2"]
