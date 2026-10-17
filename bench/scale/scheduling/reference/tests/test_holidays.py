from datetime import date

from scheduling.holidays import Calendar, easter


def test_easter():
    assert easter(2026) == date(2026, 4, 5)
    assert easter(2025) == date(2025, 4, 20)


def test_good_friday_in_the_uk():
    assert Calendar().is_holiday(date(2026, 4, 3), "UK")


def test_independence_day_only_in_the_us():
    c = Calendar()
    assert c.is_holiday(date(2026, 7, 4), "US")
    assert not c.is_holiday(date(2026, 7, 4), "UK")
