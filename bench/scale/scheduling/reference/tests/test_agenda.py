from scheduling.agenda import agenda, fmt
from scheduling.booking import Booking


def test_fmt():
    assert fmt(545) == "9:05"


def test_agenda_order():
    assert agenda([Booking("b", 630, 660), Booking("a", 600, 630)]) == ["10:00 a", "10:30 b"]
