from schemathesis import Parametrizer

from .utils import SIMPLE_PATH


def test_from_path(simple_schema):
    assert Parametrizer.from_path(SIMPLE_PATH).raw_schema.get() == simple_schema
