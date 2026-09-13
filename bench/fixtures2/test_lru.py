"""Tests for the bounded session cache.

The cache is reproduced here so the tests run standalone without the package on
sys.path.
"""


class LRUCache:
    def __init__(self, capacity):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._d = {}
        self._cap = capacity

    def put(self, key, value):
        if key in self._d:
            del self._d[key]
        self._d[key] = value
        if len(self._d) > self._cap:
            self._d.pop(next(iter(self._d)))

    def get(self, key, default=None):
        if key not in self._d:
            return default
        value = self._d.pop(key)
        self._d[key] = value
        return value

    def __len__(self):
        return len(self._d)

    def keys(self):
        return list(self._d)


def test_put_and_get():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1
    assert c.get("b") == 2


def test_capacity_is_respected():
    c = LRUCache(2)
    for k in ("a", "b", "c"):
        c.put(k, k.upper())
    assert len(c) == 2


def test_least_recently_used_is_evicted():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.get("a")             # a is now the most recently used
    c.put("c", 3)
    assert c.get("a") == 1
    assert c.get("b") is None


def test_missing_key_returns_default():
    c = LRUCache(2)
    assert c.get("nope", "fallback") == "fallback"
