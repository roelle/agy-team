from scheduling.durations import parse_all, parse_duration


def test_forms():
    assert parse_duration("1h30m") == 90
    assert parse_duration("45m") == 45
    assert parse_duration("90") == 90


def test_parse_all():
    assert parse_all(["1h", "15m", "2h5m"]) == [60, 15, 125]
