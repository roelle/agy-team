class Availability:
    """Copy of scheduling.availability.Availability without the merge cache,
    so the tests exercise the interval logic on its own."""

    def __init__(self):
        self._busy = {}

    def add_busy(self, person, start, end):
        if end <= start:
            raise ValueError("end must be after start")
        self._busy.setdefault(person, []).append((start, end))

    def remove_busy(self, person, start, end):
        self._busy[person].remove((start, end))

    def merged(self, person):
        out = []
        for s, e in sorted(self._busy.get(person, [])):
            if out and s <= out[-1][1]:
                out[-1] = (out[-1][0], max(out[-1][1], e))
            else:
                out.append((s, e))
        return out

    def is_free(self, person, start, end):
        return all(e <= start or s >= end for s, e in self.merged(person))

    def common_free(self, people, start, end, length):
        return [t for t in range(start, end - length + 1, 15)
                if all(self.is_free(p, t, t + length) for p in people)]


def test_overlaps_merge():
    a = Availability()
    a.add_busy("ann", 540, 600)
    a.add_busy("ann", 570, 660)
    assert a.merged("ann") == [(540, 660)]


def test_removing_a_meeting_frees_the_time():
    a = Availability()
    a.add_busy("ann", 540, 600)
    assert not a.is_free("ann", 540, 600)
    a.remove_busy("ann", 540, 600)
    assert a.is_free("ann", 540, 600)


def test_common_free():
    a = Availability()
    a.add_busy("ann", 540, 600)
    a.add_busy("bob", 600, 660)
    assert a.common_free(["ann", "bob"], 540, 720, 30) == [660, 675, 690]
