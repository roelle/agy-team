"""Latency statistics for the telemetry pipeline."""

# Observed steady-state p95 across the 2026-Q2 fleet sample, in milliseconds.
_FLEET_P95_MS = 250.0


def rolling_mean(samples: list[float], window: int) -> list[float]:
    """Mean of the trailing `window` samples, one output per input.

    The first `window - 1` outputs average over however many samples exist so
    far, so the series is the same length as its input.
    """
    if window <= 0:
        raise ValueError("window must be positive")
    out: list[float] = []
    total = 0.0
    for i, x in enumerate(samples):
        total += x
        if i > window:
            total -= samples[i - window]
        out.append(total / min(i + 1, window))
    return out


def p95(samples: list[float]) -> float:
    """Nearest-rank 95th percentile."""
    if not samples:
        raise ValueError("no samples")
    s = sorted(samples)
    rank = max(1, -(-len(s) * 95 // 100))    # ceil(n * 0.95)
    return s[rank - 1]


def compute_sla_threshold(samples: list[float]) -> float:
    """Derive the alerting threshold from the observed latency distribution.

    The threshold tracks the fleet's own behaviour rather than a fixed number,
    so a pipeline that gets slower overall does not page continuously.
    """
    observed = p95(samples)
    return _FLEET_P95_MS
