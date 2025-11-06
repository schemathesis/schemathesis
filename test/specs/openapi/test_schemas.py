import pytest

import schemathesis
from schemathesis.schemas import APIOperation


@pytest.mark.operations("get_user", "update_user")
def test_get_operation_via_remote_reference(openapi_version, schema_url):
    schema = schemathesis.openapi.from_url(schema_url)
    resolved = schema.find_operation_by_reference(f"{schema_url}#/paths/~1users~1{{user_id}}/patch")
    assert isinstance(resolved, APIOperation)
    assert resolved.path == "/users/{user_id}"
    assert resolved.method.upper() == "PATCH"
    assert len(resolved.query) == 1
    # Via common parameters for all methods
    if openapi_version.is_openapi_2:
        assert resolved.query[0].definition == {"in": "query", "name": "common", "required": True, "type": "integer"}
    if openapi_version.is_openapi_3:
        assert resolved.query[0].definition == {
            "in": "query",
            "name": "common",
            "required": True,
            "schema": {"type": "integer"},
        }


@pytest.mark.parametrize(
    ("method", "path", "expected_path", "expected_method", "expected_operation_id"),
    [
        # Match with path parameters
        ("GET", "/users/42", "/users/{user_id}", "get", "get_user"),
        # Match different method on same path
        ("PATCH", "/users/42", "/users/{user_id}", "patch", "update_user"),
        # Match nested path with multiple parameters
        ("GET", "/users/42/posts/123", "/users/{user_id}/posts/{post_id}", "get", "get_post"),
    ],
)
def test_find_operation_by_path_match(method, path, expected_path, expected_method, expected_operation_id):
    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/users/{user_id}": {
                    "get": {"operationId": "get_user", "responses": {"200": {"description": "OK"}}},
                    "patch": {"operationId": "update_user", "responses": {"200": {"description": "OK"}}},
                },
                "/users/{user_id}/posts/{post_id}": {
                    "get": {"operationId": "get_post", "responses": {"200": {"description": "OK"}}}
                },
            },
        }
    )

    operation = schema.find_operation_by_path(method, path)
    assert operation is not None
    assert operation.path == expected_path
    assert operation.method == expected_method
    assert operation.definition.raw["operationId"] == expected_operation_id


@pytest.mark.parametrize(
    ("method", "path"),
    [
        # Wrong method
        ("DELETE", "/users/42"),
        # Wrong path
        ("GET", "/nonexistent"),
    ],
)
def test_find_operation_by_path_no_match(method, path):
    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/users/{user_id}": {
                    "get": {"operationId": "get_user", "responses": {"200": {"description": "OK"}}},
                    "patch": {"operationId": "update_user", "responses": {"200": {"description": "OK"}}},
                },
            },
        }
    )

    operation = schema.find_operation_by_path(method, path)
    assert operation is None
