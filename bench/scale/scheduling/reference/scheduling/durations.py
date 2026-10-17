"""Durations written by people: '1h30m', '45m', '2h', '90'.

A bare number is minutes. Anything else is an error that names the input: a
meeting silently booked for 0 minutes is a meeting nobody can find.
"""
import re

_PART = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?$")


def parse_duration(text: str) -> int:
    t = (text or "").strip().lower().replace(" ", "")
    if t.isdigit():
        return int(t)
    m = _PART.match(t)
    if not t or not m or not any(m.groups()):
        raise ValueError(f"not a duration: {text!r}")
    hours, mins = (int(g) if g else 0 for g in m.groups())
    return hours * 60 + mins


def parse_all(texts: list[str]) -> list[int]:
    """Parse several; the broad except adds which one failed, and re-raises."""
    out = []
    for i, t in enumerate(texts):
        try:
            out.append(parse_duration(t))
        except Exception as e:
            raise ValueError(f"item {i}: {e}") from e
    return out
