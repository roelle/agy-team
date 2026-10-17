"""Rooms and the cleaning buffer between bookings.

BUFFER_MINUTES is per room; 0 means no buffer (the phone booths). A room not
in the table gets DEFAULT_BUFFER until facilities configure it.
"""
BUFFER_MINUTES = {"atlas": 15, "borealis": 10, "booth-1": 0, "booth-2": 0}
DEFAULT_BUFFER = 15
CAPACITY = {"atlas": 12, "borealis": 6, "booth-1": 1, "booth-2": 1}


def buffer_minutes(room: str) -> int:
    if room in BUFFER_MINUTES:
        return BUFFER_MINUTES[room]
    return DEFAULT_BUFFER


def fits(room: str, people: int) -> bool:
    return people <= CAPACITY.get(room, 0)
