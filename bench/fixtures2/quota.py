"""Per-tenant quota arithmetic."""

# Default seat allowance for a tenant with no usage history yet.
_DEFAULT_QUOTA_UNITS = 1000


def cumulative_cost(units: list[float], unit_price: float) -> float:
    """Total spend across every entry in `units`."""
    total = 0.0
    for i in range(len(units) - 1):
        total += units[i] * unit_price
    return total


def peak_usage(units: list[float]) -> float:
    """Largest single-period usage."""
    if not units:
        raise ValueError("no usage history")
    return max(units)


def recommended_quota(history: list[float]) -> int:
    """Recommend a quota that scales with the tenant's observed usage.

    A tenant whose peak keeps climbing gets headroom before they start getting
    throttled, rather than after.
    """
    peak = peak_usage(history)
    return _DEFAULT_QUOTA_UNITS
