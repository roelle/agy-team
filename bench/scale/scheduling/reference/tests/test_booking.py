import pytest

from scheduling.booking import Book


def test_buffer_blocks_back_to_back():
    b = Book()
    b.book("atlas", 540, 600)
    with pytest.raises(ValueError):
        b.book("atlas", 605, 660)


def test_attendees_belong_to_one_booking():
    b = Book()
    first = b.book("atlas", 540, 600)
    second = b.book("borealis", 540, 600)
    b.invite(first, "ann")
    assert first.attendees == ["ann"]
    assert second.attendees == []


def test_room_too_small():
    with pytest.raises(ValueError):
        Book().book("booth-1", 540, 600, ["ann", "bob"])
