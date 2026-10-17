from datetime import date

from scheduling.recurrence import skip_dates, weekly


def test_fortnightly():
    assert weekly(date(2026, 1, 5), date(2026, 2, 1), 2) == [date(2026, 1, 5), date(2026, 1, 19)]


def test_skips():
    dates = [date(2026, 1, 5), date(2026, 1, 12)]
    assert skip_dates(dates, {date(2026, 1, 12)}) == [date(2026, 1, 5)]
