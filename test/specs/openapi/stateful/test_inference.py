import pytest

import schemathesis
from schemathesis.generation.stateful.state_machine import StepOutput
from schemathesis.specs.openapi import expressions
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
            [
                {
                    "operationId": "getUserById",
                    "parameters": {
                        "userId": "$response.header.Location#regex:/users/(.+)",
                    },
                }
            ],
        ),
        # Without operationId (should use operationRef)
        (
            {
                "/users/{userId}": {
                    "get": {},
                }
            },
            "/users/123",
            [
                {
                    "operationRef": "#/paths/~1users~1{userId}/get",
                    "parameters": {
                        "userId": "$response.header.Location#regex:/users/(.+)",
                    },
                }
            ],
        ),
        # No path parameters
        (
            {"/users": {"get": {"operationId": "getUsers"}}},
            "/users",
            [{"operationId": "getUsers"}],
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
            [
                {
                    "operationId": "getUserPost",
                    "parameters": {
                        "userId": "$response.header.Location#regex:/users/(.+)/posts/[^/]+",
                        "postId": "$response.header.Location#regex:/users/[^/]+/posts/(.+)",
                    },
                }
            ],
        ),
        # Partial match - Location provides only first parameter
        (
            {
                "/users/{userId}": {
                    "get": {
                        "operationId": "getUser",
                    }
                },
                "/users/{userId}/posts/{postId}": {
                    "get": {
                        "operationId": "getUserPost",
                    }
                },
            },
            "/users/123",
            [
                {
                    "operationId": "getUser",
                    "parameters": {
                        "userId": "$response.header.Location#regex:/users/(.+)",
                    },
                },
                {
                    "operationId": "getUserPost",
                    "parameters": {
                        "userId": "$response.header.Location#regex:/users/(.+)",
                    },
                },
            ],
        ),
        # Partial match - Location provides first part of nested path
        (
            {
                "/api/v1/users/{userId}/posts/{postId}": {
                    "get": {
                        "operationId": "getUserPost",
                    }
                },
                "/api/v1/users/{userId}/posts/{postId}/comments/{commentId}": {
                    "get": {
                        "operationId": "getUserPostComment",
                    }
                },
            },
            "/api/v1/users/123/posts/456",
            [
                {
                    "operationId": "getUserPost",
                    "parameters": {
                        "postId": "$response.header.Location#regex:/api/v1/users/[^/]+/posts/(.+)",
                        "userId": "$response.header.Location#regex:/api/v1/users/(.+)/posts/[^/]+",
                    },
                },
                {
                    "operationId": "getUserPostComment",
                    "parameters": {
                        "userId": "$response.header.Location#regex:/api/v1/users/(.+)/posts/[^/]+",
                        "postId": "$response.header.Location#regex:/api/v1/users/[^/]+/posts/(.+)",
                    },
                },
            ],
        ),
        # Multiple partial matches
        (
            {
                "/users/{userId}/": {
                    "get": {
                        "operationId": "getUser",
                    }
                },
                "/users/{userId}/posts": {
                    "get": {
                        "operationId": "getUserPosts",
                    }
                },
                "/users/{userId}/posts/{postId}": {
                    "get": {
                        "operationId": "getUserPost",
                    }
                },
                "/users/{userId}/posts/{postId}/comments/{commentId}": {
                    "get": {
                        "operationId": "getUserPostComment",
                    }
                },
            },
            "/users/123/",
            [
                {
                    "operationId": "getUser",
                    "parameters": {
                        "userId": "$response.header.Location#regex:/users/(.+)/",
                    },
                },
                {
                    "operationId": "getUserPosts",
                    "parameters": {
                        "userId": "$response.header.Location#regex:/users/(.+)/",
                    },
                },
                {
                    "operationId": "getUserPost",
                    "parameters": {
                        "userId": "$response.header.Location#regex:/users/(.+)/",
                    },
                },
                {
                    "operationId": "getUserPostComment",
                    "parameters": {
                        "userId": "$response.header.Location#regex:/users/(.+)/",
                    },
                },
            ],
        ),
        # Root path
        (
            {
                "/{id}": {"get": {"operationId": "getById"}},
            },
            "/abc123",
            [
                {
                    "operationId": "getById",
                    "parameters": {
                        "id": "$response.header.Location#regex:/(.+)",
                    },
                }
            ],
        ),
        # Location doesn't match any endpoint
        (
            {"/users/{userId}": {"get": {"operationId": "getUserById"}}},
            "/orders/123",
            [],
        ),
        # No GET endpoints in schema
        (
            {"/users": {"post": {"operationId": "createUser"}}},
            "/users",
            [],
        ),
        # Empty schema
        ({}, "/users/123", []),
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
            [
                {
                    "operationId": "getUserById",
                    "parameters": {
                        "userId": "$response.header.Location#regex:/api/v1/users/(.+)",
                    },
                }
            ],
        ),
        # Path with tildes
        (
            {"/path~with~tildes/{id}": {"get": {}}},
            "/path~with~tildes/123",
            [
                {
                    "operationRef": "#/paths/~1path~0with~0tildes~1{id}/get",
                    "parameters": {
                        "id": "$response.header.Location#regex:/path~with~tildes/(.+)",
                    },
                }
            ],
        ),
    ],
)
def test_build_location_link(paths, location, link, response_factory):
    schema = schemathesis.openapi.from_dict({"openapi": "3.1.0", "paths": paths})
    router = Router.from_schema(schema)
    results = router.build_links(location)
    assert results == link
    response = response_factory.requests(headers={"Location": location})
    output = StepOutput(response=response, case=None)
    for result in results:
        if "operationRef" in result:
            assert schema.get_operation_by_reference(result["operationRef"]) is not None
        else:
            assert schema.get_operation_by_id(result["operationId"]) is not None
        for expr in result.get("parameters", {}).values():
            expressions.evaluate(expr, output)


def test_build_location_link_empty_path():
    schema = schemathesis.openapi.from_dict(
        {"openapi": "3.1.0", "paths": {"/users/{userId}": {"get": {"operationId": "getUserById"}}}}
    )
    router = Router.from_schema(schema)

    assert router.build_links("") == []
    assert router.build_links("   ") == []
    assert router.build_links("not-a-path") == []
