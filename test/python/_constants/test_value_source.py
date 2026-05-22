import random

from schemathesis.python._constants.pool import (
    ConstantEntry,
    ConstantsPool,
    ConstantsValueSource,
    Origin,
)


def _pool_with(*entries):
    pool = ConstantsPool()
    for e in entries:
        pool.add(e)
    return pool


def _entry(value, type_):
    return ConstantEntry(value=value, type=type_, origins=(Origin("s", "m", None),))


def test_value_source_active_for_populated_types():
    pool = _pool_with(_entry("active", "string"), _entry(12345, "integer"))
    source = ConstantsValueSource(pool)
    assert source.is_active("string") is True
    assert source.is_active("integer") is True
    assert source.is_active("float") is False
    assert source.is_active("bytes") is False


def test_value_source_draws_from_typed_pool():
    pool = _pool_with(_entry("active", "string"), _entry("inactive", "string"))
    source = ConstantsValueSource(pool)
    rng = random.Random(0)
    drawn = source.draw("string", rng=rng)
    assert drawn in ("active", "inactive")


def test_empty_pool_returns_none():
    source = ConstantsValueSource(ConstantsPool())
    assert source.draw("string", rng=random.Random(0)) is None
