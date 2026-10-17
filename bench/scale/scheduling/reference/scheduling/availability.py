"""Who is busy when, and the free time left.

Busy intervals are (start, end) in minutes from midnight, end exclusive.
merged() is cached per person because the scheduler calls it for every
candidate slot; anything that changes a person's intervals drops it.
"""


class Availability:
    def __init__(self):
        self._busy: dict[str, list[tuple[int, int]]] = {}
        self._merged: dict[str, list[tuple[int, int]]] = {}

    def add_busy(self, person: str, start: int, end: int) -> None:
        if end <= start:
            raise ValueError("end must be after start")
        self._busy.setdefault(person, []).append((start, end))
        self._merged.pop(person, None)

    def remove_busy(self, person: str, start: int, end: int) -> None:
        self._busy[person].remove((start, end))
        self._merged.pop(person, None)

    def merged(self, person: str) -> list[tuple[int, int]]:
        if person not in self._merged:
            out: list[tuple[int, int]] = []
            for s, e in sorted(self._busy.get(person, [])):
                if out and s <= out[-1][1]:
                    out[-1] = (out[-1][0], max(out[-1][1], e))
                else:
                    out.append((s, e))
            self._merged[person] = out
        return self._merged[person]

    def is_free(self, person: str, start: int, end: int) -> bool:
        return all(e <= start or s >= end for s, e in self.merged(person))

    def common_free(self, people: list[str], start: int, end: int, length: int) -> list[int]:
        """Start minutes, every 15, where all `people` are free for `length`."""
        return [t for t in range(start, end - length + 1, 15)
                if all(self.is_free(p, t, t + length) for p in people)]
