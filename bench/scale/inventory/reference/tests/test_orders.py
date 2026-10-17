from inventory.orders import add_note, create_order


def test_lines_and_units():
    o = create_order("acme", [("A", 2), ("B", 3)])
    assert o.total_units() == 5


def test_notes_belong_to_one_order():
    a = create_order("acme")
    b = create_order("zen")
    add_note(a, "rush")
    assert a.notes == ["rush"]
    assert b.notes == []


def test_ids_increase():
    a, b = create_order("x"), create_order("y")
    assert b.id == a.id + 1
