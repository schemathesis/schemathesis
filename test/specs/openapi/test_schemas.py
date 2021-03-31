import pytest

import schemathesis
from schemathesis.models import APIOperation
from schemathesis.specs.openapi.parameters import OpenAPI20Parameter, OpenAPI30Parameter


@pytest.mark.operations("get_user", "update_user")
def test_get_operation_via_remote_reference(openapi_version, schema_url):
    schema = schemathesis.from_uri(schema_url)
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
