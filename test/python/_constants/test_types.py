from schemathesis.python._constants.pool import ConstantEntry, Origin


def test_origin_is_frozen():
    o = Origin(source="src", module="m", adapter=None)
    try:
        o.source = "other"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Origin must be frozen")


def test_origin_equality_and_hash():
    a = Origin(source="src", module="m", adapter="fastapi")
    b = Origin(source="src", module="m", adapter="fastapi")
    c = Origin(source="src", module="m", adapter=None)
    assert a == b
    assert a != c
    assert hash(a) == hash(b)


def test_constant_entry_is_frozen():
    e = ConstantEntry(value="x", type="string", origins=(Origin("s", "m", None),))
    try:
        e.value = "y"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ConstantEntry must be frozen")


def test_constant_entry_records_multiple_origins():
    o1 = Origin(source="s1", module="m1", adapter=None)
    o2 = Origin(source="s2", module="m2", adapter="flask")
    e = ConstantEntry(value=12345, type="integer", origins=(o1, o2))
    assert e.origins == (o1, o2)
    assert e.value == 12345
