"""Proration for subscriptions that start or stop mid-period.

Periods are calendar months and both ends of a usage window are inclusive:
a customer who has the service from the 1st to the 30th of a 30-day month
has used 30 days, the whole month.
"""
import calendar
from datetime import date
from decimal import Decimal

from .money import round_cents, to_money


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def days_used(start: date, end: date) -> int:
    """Days in the window, both ends included."""
    if end < start:
        raise ValueError("end before start")
    return (end - start).days + 1


def prorate(monthly_price, start: date, end: date) -> Decimal:
    """Charge for using the service from `start` to `end` within one month."""
    if (start.year, start.month) != (end.year, end.month):
        raise ValueError("window must fall within one calendar month")
    price = to_money(monthly_price)
    full = days_in_month(start.year, start.month)
    return round_cents(price * days_used(start, end) / full)


def prorate_change(old_price, new_price, change_day: date) -> Decimal:
    """Net charge when a plan changes on `change_day`: the new plan from
    that day to the end of the month, less the unused part of the old one.
    Negative means a credit."""
    last = date(change_day.year, change_day.month,
                days_in_month(change_day.year, change_day.month))
    remaining_new = prorate(new_price, change_day, last)
    remaining_old = prorate(old_price, change_day, last)
    return remaining_new - remaining_old
