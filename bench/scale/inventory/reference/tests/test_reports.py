from inventory.reports import busiest, shrinkage


def test_busiest_has_n():
    assert len(busiest(5)) == 5


def test_shrinkage_is_positive():
    assert all(v > 0 for v in shrinkage().values())
