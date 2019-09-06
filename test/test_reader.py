import pytest

from schemathesis import readers

from .utils import SIMPLE_PATH


@pytest.mark.parametrize(
    "method, path", ((readers.from_path, SIMPLE_PATH), (readers.from_uri, f"file://{SIMPLE_PATH}"))
)
def test_reader(simple_schema, method, path):
    # Each reader method should read the specified schema correctly
    assert method(path) == simple_schema
