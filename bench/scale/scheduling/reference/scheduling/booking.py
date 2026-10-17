"""Room bookings."""
from dataclasses import dataclass, field

from .rooms import buffer_minutes, fits


@dataclass
class Booking:
    room: str
    start: int          # minutes from midnight
    end: int
    attendees: list[str] = field(default_factory=list)


class Book:
    def __init__(self):
        self.bookings: list[Booking] = []

    def book(self, room: str, start: int, end: int, attendees=None) -> Booking:
        """Book a room, refusing overlaps once each booking's cleaning buffer
        is counted after it, and refusing a room too small."""
        people = list(attendees) if attendees else []
        if people and not fits(room, len(people)):
            raise ValueError(f"{room} cannot hold {len(people)}")
        for b in self.bookings:
            if b.room == room and start < b.end + buffer_minutes(room) and b.start < end + buffer_minutes(room):
                raise ValueError(f"{room} is taken {b.start}-{b.end}")
        booking = Booking(room, start, end, people)
        self.bookings.append(booking)
        return booking

    def invite(self, booking: Booking, person: str) -> None:
        booking.attendees.append(person)
