import pytest

import schemathesis
from schemathesis.utils import is_schemathesis_test

from .utils import SIMPLE_PATH

MINIMAL_SCHEMA = {"swagger": "2.0"}


@pytest.mark.parametrize(
    "method, path", ((schemathesis.from_path, SIMPLE_PATH), (schemathesis.from_uri, f"file://{SIMPLE_PATH}"))
)
def test_alternative_constructors(simple_schema, method, path):
    assert method(path).raw_schema == simple_schema


def test_is_schemathesis_test():
    # When a test is wrapped into with `parametrize`
    schema = schemathesis.from_dict(MINIMAL_SCHEMA)

    @schema.parametrize()
    def test_a():
        pass

    # Then is should be recognized as a schemathesis test
    assert is_schemathesis_test(test_a)
