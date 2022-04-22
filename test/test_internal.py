import pytest

from schemathesis import internal, runner
from schemathesis.specs.openapi._hypothesis import _PARAMETER_STRATEGIES_CACHE


@pytest.mark.operations("custom_format")
def test_clear_cache(openapi3_schema, simple_schema):
    list(runner.from_schema(openapi3_schema).execute())
    assert len(_PARAMETER_STRATEGIES_CACHE) >= 1
    internal.clear_cache()
    assert len(_PARAMETER_STRATEGIES_CACHE) == 0
