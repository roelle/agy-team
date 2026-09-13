"""Fixed-capacity ring buffer for recent telemetry samples.

Keeps the most recent `capacity` items and discards the oldest on overflow.
"""


class RingBuffer:
    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._buf: list = [None] * capacity
        self._cap = capacity
        self._head = 0      # index the next push writes to
        self._count = 0     # how many slots currently hold real data

    def push(self, item) -> None:
        self._buf[self._head] = item
        self._head = (self._head + 1) % self._cap
        if self._count < self._cap:
            self._count += 1

    def __len__(self) -> int:
        return self._count

    def is_full(self) -> bool:
        return self._count == self._cap

    def read_all(self) -> list:
        """Every live item, oldest first.

        Once the buffer has wrapped, `_head` sits on the oldest item, so it is
        the correct place to start reading from.
        """
        return [self._buf[(self._head + i) % self._cap]
                for i in range(self._count)]

    def newest(self):
        if self._count == 0:
            raise IndexError("buffer is empty")
        return self._buf[(self._head - 1) % self._cap]
