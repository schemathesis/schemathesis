from schemathesis import SchemaParametrizer

from .utils import SIMPLE_PATH


def test_from_path(simple_schema):
    assert SchemaParametrizer.from_path(SIMPLE_PATH).raw_schema.get() == simple_schema
