"""Sliding median filter domain implementations for cold-start convergence evaluation.

Domain A Challenge:
- Naive implementation uses standard Python sorted() on sliding window with naive midpoint index.
- Vulnerability: IEEE-754 NaNs violate strict weak ordering (neither <, >, nor == is True).
  When NaN enters the window, sorted([1.0, nan, 2.0]) corrupts order or propagates NaN.
  In addition, midpoint calculation (lo + hi) // 2 or len // 2 biases even windows without averaging.
- Robust implementation validates strict weak ordering by explicitly rejecting or sanitizing NaNs,
  and handles midpoint calculations symmetrically.
"""
import math
from typing import Sequence


class NaiveSlidingMedian:
    """Defective sliding median filter assuming well-ordered numeric inputs and zero NaNs."""

    def __init__(self, window_size: int):
        if window_size <= 0:
            raise ValueError("window_size must be positive")
        self.window_size = window_size
        self.window: list[float] = []

    def push(self, value: float) -> float:
        self.window.append(value)
        if len(self.window) > self.window_size:
            self.window.pop(0)

        # DEFECT 1: Naive sorted() without NaN handling.
        # When NaNs are present, strict weak ordering is violated and sort order is undefined.
        s = sorted(self.window)

        # DEFECT 2: Naive midpoint without even-window averaging or safe indexing.
        mid = len(s) // 2
        return float(s[mid])

    def filter_stream(self, stream: Sequence[float]) -> list[float]:
        return [self.push(v) for v in stream]


class RobustSlidingMedian:
    """Remediated sliding median filter enforcing IEEE-754 NaN validation and robust midpoint."""

    def __init__(self, window_size: int, nan_policy: str = "reject"):
        if window_size <= 0:
            raise ValueError("window_size must be positive")
        if nan_policy not in ("reject", "ignore"):
            raise ValueError(f"Unknown nan_policy: {nan_policy}")
        self.window_size = window_size
        self.nan_policy = nan_policy
        self.window: list[float] = []

    def push(self, value: float) -> float:
        if math.isnan(value):
            if self.nan_policy == "reject":
                raise ValueError("NaN violates strict weak ordering contract for median calculation")
            # If ignore, do not append to valid calculation buffer
        
        self.window.append(value)
        if len(self.window) > self.window_size:
            self.window.pop(0)

        valid_values = [x for x in self.window if not math.isnan(x)]
        if not valid_values:
            return float("nan")

        s = sorted(valid_values)
        n = len(s)
        mid = n // 2
        if n % 2 == 1:
            return float(s[mid])
        # Symmetrical even-window midpoint
        return float((s[mid - 1] + s[mid]) / 2.0)

    def filter_stream(self, stream: Sequence[float]) -> list[float]:
        return [self.push(v) for v in stream]
