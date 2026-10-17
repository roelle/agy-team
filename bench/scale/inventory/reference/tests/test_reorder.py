from inventory.reorder import needs_reorder, reorder_quantity, safety_stock


def test_below_the_point_reorders():
    assert needs_reorder(5, 20)


def test_well_above_does_not():
    assert not needs_reorder(50, 20)


def test_safety_stock_rounds_up():
    assert safety_stock(2.2, 3) == 11


def test_quantity_brings_it_back_up():
    assert reorder_quantity(5, 20, 100) == 95
