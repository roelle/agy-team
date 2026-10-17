"""A day's agenda, in time order, formatted for the sidebar."""


def fmt(minutes: int) -> str:
    """9:05, 13:30 -- no leading zero on the hour, as the UI shows it."""
    h, m = divmod(minutes, 60)
    return f"{h}:{m:02d}"


def agenda(bookings) -> list[str]:
    """'9:00 atlas', ... sorted by start time."""
    ordered = sorted(bookings, key=lambda b: b.start)
    return [f"{fmt(b.start)} {b.room}" for b in ordered]
