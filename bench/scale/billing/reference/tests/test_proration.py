from datetime import date
from decimal import Decimal

import pytest

from billing.proration import days_used, prorate, prorate_change


def test_whole_month():
    assert prorate("30.00", date(2026, 4, 1), date(2026, 4, 30)) == Decimal("30.00")


def test_single_day_counts_as_one():
    assert days_used(date(2026, 4, 3), date(2026, 4, 3)) == 1


def test_window_across_months_is_refused():
    with pytest.raises(ValueError):
        prorate("30.00", date(2026, 4, 20), date(2026, 5, 2))


def test_upgrade_mid_month_costs_the_difference():
    assert prorate_change("30.00", "60.00", date(2026, 4, 16)) == Decimal("15.00")
