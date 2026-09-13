"""Bounded session cache with least-recently-used eviction."""


class LRUCache:
    """Keeps at most `capacity` entries, discarding the least recently used.

    Backed by a dict, which preserves insertion order, so the oldest key is
    always the first one iteration yields.
    """

    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._d: dict = {}
        self._cap = capacity

    def put(self, key, value) -> None:
        if key in self._d:
            del self._d[key]
        self._d[key] = value
        if len(self._d) > self._cap:
            self._d.pop(next(iter(self._d)))

    def get(self, key, default=None):
        return self._d.get(key, default)

    def __len__(self) -> int:
        return len(self._d)

    def keys(self) -> list:
        """Live keys, least recently used first."""
        return list(self._d)
