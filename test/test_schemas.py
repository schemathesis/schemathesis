import pytest

from schemathesis import SwaggerV20
from schemathesis.schemas import wrap_schema


@pytest.fixture()
def swagger_20(simple_schema):
    return SwaggerV20(simple_schema)


@pytest.mark.parametrize("base_path", ("/v1", "/v1/"))
def test_base_path_suffix(swagger_20, base_path):
    # When suffix is present or not present in the raw schema's "basePath"
    swagger_20.raw_schema["basePath"] = base_path
    # Then base path ends with "/" anyway in the swagger instance
    assert swagger_20.base_path == "/v1/"


def test_wrap_schema_unsupported_type():
    with pytest.raises(ValueError, match="^Unsupported schema type$"):
        wrap_schema({})
