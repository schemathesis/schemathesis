import pytest

import schemathesis

from .utils import SIMPLE_PATH


@pytest.mark.parametrize(
    "method, path", ((schemathesis.from_path, SIMPLE_PATH), (schemathesis.from_uri, f"file://{SIMPLE_PATH}"))
)
def test_reader(simple_schema, method, path):
    # Each reader method should read the specified schema correctly
    assert method(path).raw_schema == simple_schema


@pytest.mark.parametrize(
    "method, path, message",
    (
        (
            schemathesis.Parametrizer.from_path,
            SIMPLE_PATH,
            r"^`Parametrizer.from_path` is deprecated, use `schemathesis.from_path` instead.\Z",
        ),
        (
            schemathesis.Parametrizer.from_uri,
            f"file://{SIMPLE_PATH}",
            r"^`Parametrizer.from_uri` is deprecated, use `schemathesis.from_uri` instead.\Z",
        ),
    ),
)
def test_backward_compatibility(simple_schema, method, path, message):
    # Each reader method should read the specified schema correctly
    with pytest.warns(DeprecationWarning, match=message):
        assert method(path).raw_schema == simple_schema
