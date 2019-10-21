import pytest
from jsonschema import RefResolver


@pytest.mark.parametrize("base_path", ("/v1", "/v1/"))
def test_base_path_suffix(swagger_20, base_path):
    # When suffix is present or not present in the raw schema's "basePath"
    swagger_20.raw_schema["basePath"] = base_path
    # Then base path ends with "/" anyway in the swagger instance
    assert swagger_20.base_path == "/v1/"


def test_resolver_cache(swagger_20, mocker):
    spy = mocker.patch("schemathesis.schemas.jsonschema.RefResolver", wraps=RefResolver)
    assert "_resolver" not in swagger_20.__dict__
    assert isinstance(swagger_20.resolver, RefResolver)
    assert spy.call_count == 1
    # Cached
    assert "_resolver" in swagger_20.__dict__
    assert isinstance(swagger_20.resolver, RefResolver)
    assert spy.call_count == 1
