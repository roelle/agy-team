from datetime import datetime

from scheduling.slots import day_slots


def test_hour_slots():
    s = day_slots(datetime(2026, 3, 2, 9), datetime(2026, 3, 2, 12, 10), 60)
    assert [t.hour for t in s] == [9, 10, 11]


def test_a_slot_that_does_not_fit_is_left_out():
    s = day_slots(datetime(2026, 3, 2, 9), datetime(2026, 3, 2, 10, 15), 30)
    assert len(s) == 2
