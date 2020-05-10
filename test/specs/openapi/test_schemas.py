import pytest

from schemathesis.models import Endpoint


@pytest.mark.endpoints("get_user", "update_user")
def test_get_endpoint_via_remote_reference(swagger_20, schema_url):
    resolved = swagger_20.get_endpoint_by_reference(f"{schema_url}#/paths/~1users~1{{user_id}}/patch")
    assert isinstance(resolved, Endpoint)
    assert resolved.path == "/v1/users/{user_id}"
    assert resolved.method == "PATCH"
    # Via common parameters for all methods
    assert resolved.query == {
        "properties": {"common": {"in": "query", "name": "common", "type": "integer"},},
        "additionalProperties": False,
        "type": "object",
        "required": ["common"],
    }
