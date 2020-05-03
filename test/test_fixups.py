import pytest

from schemathesis import fixups


def test_global_fixup(testdir, fast_api_schema):
    testdir.makepyfile(
        """
import schemathesis
from hypothesis import settings

schemathesis.fixups.install()
schema = schemathesis.from_dict({schema})

def teardown_module(module):
    schemathesis.fixups.uninstall()
    assert schemathesis.hooks.get_all_by_name("before_load_schema") == []

@schema.parametrize()
@settings(max_examples=1)
def test(case):
    assert 0 < case.query["value"] < 10
    """.format(
            schema=fast_api_schema
        ),
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)


@pytest.mark.parametrize(
    "value, expected",
    (
        # No-op case
        ({"exclusiveMinimum": True, "minimum": 5}, {"exclusiveMinimum": True, "minimum": 5}),
        # Draft 7 to Draft 4
        ({"exclusiveMinimum": 5}, {"exclusiveMinimum": True, "minimum": 5}),
        ({"exclusiveMaximum": 5}, {"exclusiveMaximum": True, "maximum": 5}),
        # Nested cases
        ({"schema": {"exclusiveMaximum": 5}}, {"schema": {"exclusiveMaximum": True, "maximum": 5}}),
        ([{"schema": {"exclusiveMaximum": 5}}], [{"schema": {"exclusiveMaximum": True, "maximum": 5}}]),
    ),
)
def test_fastapi_schema_conversion(value, expected):
    fixups.fast_api.before_load_schema(None, value)
    assert value == expected
