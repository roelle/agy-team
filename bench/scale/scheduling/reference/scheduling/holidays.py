"""Public holidays by year and region, from a calculator, cached per
(year, region): computing Easter-relative dates is not free."""
from datetime import date, timedelta


def easter(year: int) -> date:
    """Western Easter (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def compute(year: int, region: str) -> set[date]:
    days = {date(year, 1, 1), date(year, 12, 25)}
    if region in ("UK", "DE"):
        e = easter(year)
        days |= {e - timedelta(days=2), e + timedelta(days=1)}
    if region == "US":
        days |= {date(year, 7, 4)}
    return days


class Calendar:
    def __init__(self):
        self._cache: dict[tuple[int, str], set[date]] = {}

    def holidays(self, year: int, region: str) -> set[date]:
        key = (year, region)
        if key not in self._cache:
            self._cache[key] = compute(year, region)
        return self._cache[key]

    def is_holiday(self, d: date, region: str) -> bool:
        return d in self.holidays(d.year, region)
