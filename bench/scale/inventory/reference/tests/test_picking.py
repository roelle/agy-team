from inventory.picking import walk_order, waves


def lines(*bins):
    return [{"sku": f"s{i}", "qty": 1, "bin": b} for i, b in enumerate(bins)]


def test_walk_order_by_aisle():
    assert [l["bin"] for l in walk_order(lines("C-1", "A-3", "B-2"))] == ["A-3", "B-2", "C-1"]


def test_even_waves():
    assert [len(w) for w in waves(lines("A-1", "A-2", "A-3", "A-4"), 2)] == [2, 2]
