from schemathesis.python._constants.pool import ConstantEntry, ConstantsPool, Origin
from test.python._constants.helpers import pool_values

ORIGIN = Origin(source="s", module="m", adapter=None)


def _entry(value):
    return ConstantEntry(value=value, type="string", origins=(ORIGIN,))


def test_pool_evicts_oldest_when_cap_exceeded():
    pool = ConstantsPool(cap_per_type=2)
    for value in ["first_value", "second_value", "third_value"]:
        pool.add(_entry(value))
    assert pool_values(pool, "string") == ("second_value", "third_value")
