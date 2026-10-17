"""Pick lists: which bin to walk to, in walking order, in waves.

Bins are named '<aisle>-<slot>' ('B-7', 'B-12'). Walking order is aisle by
aisle, and within an aisle by slot NUMBER: B-7 comes before B-12.
"""


def bin_key(location: str) -> tuple[str, int]:
    aisle, _, slot = location.partition("-")
    return (aisle, int(slot))


def walk_order(lines: list[dict]) -> list[dict]:
    """Order lines ({'sku', 'qty', 'bin'}) the way a picker walks."""
    return sorted(lines, key=lambda l: bin_key(l["bin"]))


def waves(lines: list[dict], size: int) -> list[list[dict]]:
    """Split walk-ordered lines into waves of at most `size` lines. The last
    wave may be short; every line is in exactly one wave."""
    if size <= 0:
        raise ValueError("wave size must be positive")
    ordered = walk_order(lines)
    return [ordered[i:i + size] for i in range(0, len(ordered), size)]
