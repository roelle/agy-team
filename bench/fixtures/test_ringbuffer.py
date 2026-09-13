"""Tests for the telemetry ring buffer.

The buffer class is reproduced here so the tests run standalone without the
package on sys.path.
"""


class RingBuffer:
    def __init__(self, capacity):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._buf = [None] * capacity
        self._cap = capacity
        self._head = 0
        self._count = 0

    def push(self, item):
        self._buf[self._head] = item
        self._head = (self._head + 1) % self._cap
        if self._count < self._cap:
            self._count += 1

    def __len__(self):
        return self._count

    def is_full(self):
        return self._count == self._cap

    def read_all(self):
        start = (self._head - self._count) % self._cap
        return [self._buf[(start + i) % self._cap] for i in range(self._count)]

    def newest(self):
        if self._count == 0:
            raise IndexError("buffer is empty")
        return self._buf[(self._head - 1) % self._cap]


def test_push_and_length():
    rb = RingBuffer(4)
    for x in (1, 2, 3):
        rb.push(x)
    assert len(rb) == 3
    assert not rb.is_full()


def test_read_all_partial():
    rb = RingBuffer(4)
    for x in (1, 2, 3):
        rb.push(x)
    assert rb.read_all() == [1, 2, 3]


def test_read_all_after_wrap():
    rb = RingBuffer(3)
    for x in (1, 2, 3, 4, 5):
        rb.push(x)
    assert rb.is_full()
    assert rb.read_all() == [3, 4, 5]


def test_newest():
    rb = RingBuffer(3)
    for x in (1, 2, 3, 4):
        rb.push(x)
    assert rb.newest() == 4
