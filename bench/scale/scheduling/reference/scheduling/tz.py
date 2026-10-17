"""Converting meeting times between zones.

Times are stored in UTC and shown in the viewer's zone. Conversion goes
through zoneinfo, so it is right on both sides of a daylight-saving change;
an ambiguous local time (the repeated hour in autumn) is taken as its first
occurrence, fold=0, which is what the calendar UI shows.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def to_utc(local: datetime, zone: str) -> datetime:
    """A naive local time in `zone` -> an aware UTC time."""
    if local.tzinfo is not None:
        raise ValueError("pass a naive local time")
    return local.replace(tzinfo=ZoneInfo(zone), fold=0).astimezone(timezone.utc)


def from_utc(utc: datetime, zone: str) -> datetime:
    """An aware UTC time -> a naive local time in `zone`."""
    if utc.tzinfo is None:
        raise ValueError("pass an aware UTC time")
    return utc.astimezone(ZoneInfo(zone)).replace(tzinfo=None)


def same_wall_clock(a: datetime, zone_a: str, zone_b: str) -> datetime:
    """The local time in zone_b at the moment it is `a` in zone_a."""
    return from_utc(to_utc(a, zone_a), zone_b)
