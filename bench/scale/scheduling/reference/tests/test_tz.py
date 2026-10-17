from datetime import datetime

from scheduling.tz import from_utc, same_wall_clock, to_utc


def test_new_york_winter():
    assert to_utc(datetime(2026, 1, 15, 9), "America/New_York").hour == 14


def test_new_york_summer():
    assert to_utc(datetime(2026, 7, 15, 9), "America/New_York").hour == 13


def test_round_trip():
    local = datetime(2026, 7, 15, 9, 30)
    assert from_utc(to_utc(local, "Europe/London"), "Europe/London") == local


def test_wall_clock_across_zones():
    assert same_wall_clock(datetime(2026, 7, 15, 9), "Europe/London", "Asia/Tokyo").hour == 17
