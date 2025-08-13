import pytest

import schemathesis
from schemathesis.specs.openapi.stateful.inference import Router


@pytest.mark.parametrize(
    ["paths", "location", "link"],
    [
        (
            {
                "/users/{userId}": {
                    "get": {
                        "operationId": "getUserById",
                    }
                },
            },
            "/users/123",
            {
                "operationId": "getUserById",
                "parameters": {
                    "userId": "$response.header.Location#regex:/users/(.+)",
                },
            },
        ),
        # Without operationId (should use operationRef)
        (
            {
                "/users/{userId}": {
                    "get": {},
                }
            },
            "/users/123",
            {
                "operationRef": "#/paths/~1users~1{userId}/get",
                "parameters": {
                    "userId": "$response.header.Location#regex:/users/(.+)",
                },
            },
        ),
        # No path parameters
        (
            {"/users": {"get": {"operationId": "getUsers"}}},
            "/users",
            {"operationId": "getUsers"},
        ),
        # Multiple path parameters
        (
            {
                "/users/{userId}/posts/{postId}": {
                    "get": {
                        "operationId": "getUserPost",
                    }
                }
            },
            "/users/123/posts/456",
            {
                "operationId": "getUserPost",
                "parameters": {
                    "userId": "$response.header.Location#regex:/users/(.+)/posts/[^/]+",
                    "postId": "$response.header.Location#regex:/users/[^/]+/posts/(.+)",
                },
            },
        ),
        # Root path
        (
            {
                "/{id}": {"get": {"operationId": "getById"}},
            },
            "/abc123",
            {
                "operationId": "getById",
                "parameters": {
                    "id": "$response.header.Location#regex:/(.+)",
                },
            },
        ),
        # Location doesn't match any endpoint
        (
            {"/users/{userId}": {"get": {"operationId": "getUserById"}}},
            "/orders/123",
            None,
        ),
        # No GET endpoints in schema
        (
            {"/users": {"post": {"operationId": "createUser"}}},
            "/users",
            None,
        ),
        # Empty schema
        ({}, "/users/123", None),
        # Complex path with special characters
        (
            {
                "/api/v1/users/{userId}": {
                    "get": {
                        "operationId": "getUserById",
                    }
                }
            },
            "/api/v1/users/user-123-abc",
            {
                "operationId": "getUserById",
                "parameters": {
                    "userId": "$response.header.Location#regex:/api/v1/users/(.+)",
                },
            },
        ),
        # Path with tildes
        (
            {"/path~with~tildes/{id}": {"get": {}}},
            "/path~with~tildes/123",
            {
                "operationRef": "#/paths/~1path~0with~0tildes~1{id}/get",
                "parameters": {
                    "id": "$response.header.Location#regex:/path~with~tildes/(.+)",
                },
            },
        ),
    ],
)
def test_build_location_link(paths, location, link):
    schema = schemathesis.openapi.from_dict({"openapi": "3.1.0", "paths": paths})
    router = Router.from_schema(schema)
    result = router.build_link(location)
    assert result == link
    if result is not None:
        if "operationRef" in result:
            assert schema.get_operation_by_reference(result["operationRef"]) is not None
        else:
            assert schema.get_operation_by_id(result["operationId"]) is not None


def test_build_location_link_empty_path():
    schema = schemathesis.openapi.from_dict(
        {"openapi": "3.1.0", "paths": {"/users/{userId}": {"get": {"operationId": "getUserById"}}}}
    )
    router = Router.from_schema(schema)

    assert router.build_link("") is None
    assert router.build_link("   ") is None
    assert router.build_link("not-a-path") is None
