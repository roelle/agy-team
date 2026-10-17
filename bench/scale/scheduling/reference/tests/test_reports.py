from scheduling.reports import busiest_days, minutes_used


def test_every_room_is_reported():
    assert set(minutes_used()) == {"atlas", "borealis", "booth-1", "booth-2"}


def test_busiest_days():
    assert len(busiest_days("atlas", 3)) == 3
