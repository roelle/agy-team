from datetime import date

import pytest

from billing.dunning import level, notices, parse_due


def test_due_today_is_not_late():
    assert level(date(2026, 3, 1), date(2026, 3, 1)) == 0


def test_levels():
    due = date(2026, 3, 1)
    assert level(due, date(2026, 3, 2)) == 1
    assert level(due, date(2026, 3, 20)) == 2
    assert level(due, date(2026, 5, 1)) == 3


def test_notices_group_and_sort():
    today = date(2026, 3, 10)
    out = notices({"INV-9": date(2026, 3, 5), "INV-2": date(2026, 3, 1)}, today)
    assert out == {1: ["INV-2", "INV-9"]}


def test_bad_date_names_the_input():
    with pytest.raises(ValueError, match="2026-13-01"):
        parse_due("2026-13-01")
