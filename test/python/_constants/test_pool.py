from schemathesis.python._constants.pool import (
    ConstantEntry,
    ConstantsPool,
    Origin,
)


def _entry(value, type_, source="s", module="m", adapter=None):
    return ConstantEntry(value=value, type=type_, origins=(Origin(source, module, adapter),))


def test_pool_groups_by_type():
    pool = ConstantsPool()
    pool.add(_entry("active", "string"))
    pool.add(_entry(12345, "integer"))

    assert pool.values_for("string") == ("active",)
    assert pool.values_for("integer") == (12345,)
    assert pool.values_for("float") == ()


def test_pool_merges_origins_on_identical_value():
    pool = ConstantsPool()
    pool.add(_entry("x", "string", source="s1", module="m1"))
    pool.add(_entry("x", "string", source="s2", module="m2", adapter="fastapi"))

    entries = pool.entries_for("string")
    assert len(entries) == 1
    origins = entries[0].origins
    sources = {o.source for o in origins}
    assert sources == {"s1", "s2"}


def test_pool_enforces_cap_per_type():
    pool = ConstantsPool(cap_per_type=3)
    for i in range(10):
        pool.add(_entry(f"s{i}", "string"))
    assert len(pool.entries_for("string")) == 3
    assert {e.value for e in pool.entries_for("string")} == {"s7", "s8", "s9"}


def test_empty_pool_value_lookups_are_empty_tuples():
    pool = ConstantsPool()
    for t in ("string", "integer", "float", "bytes"):
        assert pool.values_for(t) == ()


def test_is_empty():
    pool = ConstantsPool()
    assert pool.is_empty() is True
    pool.add(_entry("x", "string"))
    assert pool.is_empty() is False
