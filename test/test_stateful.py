import pytest

from schemathesis.stateful import ParsedData
from schemathesis.utils import NOT_SET


@pytest.mark.parametrize(
    "parameters, body", (({"a": 1}, None), ({"a": 1}, NOT_SET), ({"a": 1}, {"value": 1}), ({"a": 1}, [1, 2, 3]))
)
def test_hashable(parameters, body):
    hash(ParsedData(parameters, body))
