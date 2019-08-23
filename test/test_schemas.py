import pytest

from schemathesis import SwaggerV20
from schemathesis.schemas import traverse_schema


@pytest.fixture()
def swagger_20(simple_schema):
    return SwaggerV20(simple_schema)


@pytest.mark.parametrize("base_path", ("/v1", "/v1/"))
def test_base_path_suffix(swagger_20, base_path):
    # When suffix is present or not present in the raw schema's "basePath"
    swagger_20.raw_schema["basePath"] = base_path
    # Then base path ends with "/" anyway in the swagger instance
    assert swagger_20.base_path == "/v1/"


def test_traverse_schema(simple_schema):
    assert list(traverse_schema(simple_schema)) == [
        (["swagger"], "2.0"),
        (["info", "title"], "Sample API"),
        (["info", "description"], "API description in Markdown."),
        (["info", "version"], "1.0.0"),
        (["host"], "api.example.com"),
        (["basePath"], "/v1"),
        (["schemes"], ["https"]),
        (["paths", "/users", "get", "summary"], "Returns a list of users."),
        (["paths", "/users", "get", "description"], "Optional extended description in Markdown."),
        (["paths", "/users", "get", "produces"], ["application/json"]),
        (["paths", "/users", "get", "responses", 200, "description"], "OK"),
    ]
