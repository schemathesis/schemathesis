import schemathesis

from .utils import SIMPLE_PATH


def test_from_path(simple_schema):
    assert schemathesis.from_path(SIMPLE_PATH).raw_schema == simple_schema
