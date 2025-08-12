import pytest

import schemathesis
from schemathesis.core.errors import InvalidSchema
from schemathesis.schemas import APIOperation
from schemathesis.specs.openapi.parameters import OpenAPI20Parameter, OpenAPI30Parameter
from schemathesis.specs.openapi.schemas import check_header


@pytest.mark.operations("get_user", "update_user")
def test_get_operation_via_remote_reference(openapi_version, schema_url):
    schema = schemathesis.openapi.from_url(schema_url)
    resolved = schema.get_operation_by_reference(f"{schema_url}#/paths/~1users~1{{user_id}}/patch")
    assert isinstance(resolved, APIOperation)
    assert resolved.path == "/users/{user_id}"
    assert resolved.method.upper() == "PATCH"
    assert len(resolved.query) == 1
    # Via common parameters for all methods
    if openapi_version.is_openapi_2:
        assert isinstance(resolved.query[0], OpenAPI20Parameter)
        assert resolved.query[0].definition == {"in": "query", "name": "common", "required": True, "type": "integer"}
    if openapi_version.is_openapi_3:
        assert isinstance(resolved.query[0], OpenAPI30Parameter)
        assert resolved.query[0].definition == {
            "in": "query",
            "name": "common",
            "required": True,
            "schema": {"type": "integer"},
        }


@pytest.mark.parametrize(
    ["parameter", "expected"],
    [
        ({"name": ""}, "Header name should not be empty"),
        ({"name": "Invalid\x80Name"}, "Header name should be ASCII: Invalid\x80Name"),
        ({"name": "\nInvalid"}, "Invalid leading whitespace"),
    ],
)
def test_check_header_errors(parameter, expected):
    with pytest.raises(InvalidSchema, match=expected):
        check_header(parameter)
