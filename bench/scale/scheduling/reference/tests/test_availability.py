from scheduling.availability import Availability


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
