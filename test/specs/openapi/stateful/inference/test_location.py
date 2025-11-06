import pytest
from flask import Flask, jsonify, request

import schemathesis
from schemathesis.engine.observations import LocationHeaderEntry
from schemathesis.generation.stateful.state_machine import StepOutput
from schemathesis.specs.openapi import expressions
from schemathesis.specs.openapi.stateful.inference import LinkInferencer


def assert_links_work(response_factory, location, results, schema):
    # Verify links are valid and expressions work
    response = response_factory.requests(headers={"Location": location})
    output = StepOutput(response=response, case=None)
    for result in results:
        if "operationRef" in result:
            operation = schema.find_operation_by_reference(result["operationRef"])
        else:
            operation = schema.find_operation_by_id(result["operationId"])
        assert operation is not None
        for expr in result.get("parameters", {}).values():
            expressions.evaluate(expr, output)


def build_links(inferencer, location: str) -> list[dict]:
    """Build all possible OpenAPI link definitions from Location header."""
    normalized_location = inferencer._normalize_location(location)
    if normalized_location is None:
        return []
    matches = inferencer._find_matches_from_normalized_location(normalized_location)
    if matches is None:
        return []
    return inferencer._build_links_from_matches(matches)


def link_by_id(operation_id: str, **parameters):
    return _link_by("operationId", operation_id, **parameters)


def link_by_ref(ref: str, **parameters):
    return _link_by("operationRef", ref, **parameters)


def _link_by(key: str, value: str, **parameters):
    return {
        key: value,
        "x-schemathesis": {"is_inferred": True},
        "parameters": {key: f"$response.header.Location#regex:{regex}" for key, regex in parameters.items()},
    }


@pytest.mark.parametrize(
    ["paths", "location", "expected"],
    [
        (
            {
                "/users": {
                    "post": {
                        "operationId": "createUser",
                    }
                },
                "/users/{userId}": {
                    "get": {
                        "operationId": "getUserById",
                    }
                },
            },
            "/users/123",
            [link_by_id("getUserById", userId="/users/(.+)")],
        ),
        # Without operationId (should use operationRef)
        (
            {
                "/users/{userId}": {
                    "get": {},
                }
            },
            "/users/123",
            [link_by_ref("#/paths/~1users~1{userId}/get", userId="/users/(.+)")],
        ),
        # No path parameters
        # Links without parameters don't make sense
        (
            {"/users": {"get": {"operationId": "getUsers"}}},
            "/users",
            [],
        ),
        (
            {
                "/users": {
                    "get": {"operationId": "listUsers"},
                    "post": {"operationId": "createUser"},
                    "delete": {"operationId": "deleteAllUsers"},
                }
            },
            "/users",
            [],
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
                link_by_id(
                    "getUserPost",
                    userId="/users/(.+)/posts/[^/]+",
                    postId="/users/[^/]+/posts/(.+)",
                )
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
                link_by_id("getUser", userId="/users/(.+)"),
                link_by_id("getUserPost", userId="/users/(.+)"),
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
                link_by_id(
                    "getUserPost",
                    userId="/api/v1/users/(.+)/posts/[^/]+",
                    postId="/api/v1/users/[^/]+/posts/(.+)",
                ),
                link_by_id(
                    "getUserPostComment",
                    userId="/api/v1/users/(.+)/posts/[^/]+",
                    postId="/api/v1/users/[^/]+/posts/(.+)",
                ),
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
                link_by_id("getUser", userId="/users/(.+)/"),
                link_by_id("getUserPosts", userId="/users/(.+)/"),
                link_by_id("getUserPost", userId="/users/(.+)/"),
                link_by_id("getUserPostComment", userId="/users/(.+)/"),
            ],
        ),
        # Root path
        (
            {
                "/{id}": {"get": {"operationId": "getById"}},
            },
            "/abc123",
            [link_by_id("getById", id="/(.+)")],
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
            [link_by_id("getUserById", userId="/api/v1/users/(.+)")],
        ),
        # Path with tildes
        (
            {"/path~with~tildes/{id}": {"get": {}}},
            "/path~with~tildes/123",
            [link_by_ref("#/paths/~1path~0with~0tildes~1{id}/get", id="/path~with~tildes/(.+)")],
        ),
    ],
)
def test_build_location_link(paths, location, expected, response_factory):
    schema = schemathesis.openapi.from_dict(
        {"openapi": "3.1.0", "info": {"title": "Test API", "version": "0.0.1"}, "paths": paths}
    )
    inferencer = LinkInferencer.from_schema(schema)
    results = build_links(inferencer, location)
    assert results == expected
    if results:
        assert_links_work(response_factory, location, results, schema)


def test_build_location_link_empty_path():
    schema = schemathesis.openapi.from_dict(
        {"openapi": "3.1.0", "paths": {"/users/{userId}": {"get": {"operationId": "getUserById"}}}}
    )
    inferencer = LinkInferencer.from_schema(schema)

    assert build_links(inferencer, "") == []
    assert build_links(inferencer, "   ") == []
    assert build_links(inferencer, "not-a-path") == []


@pytest.mark.parametrize(
    ["base_url", "paths", "location", "expected"],
    [
        # Relative Location with base_url - should work normally
        (
            "http://localhost:8080/api/v1",
            {"/users/{userId}": {"get": {"operationId": "getUserById"}}},
            "/users/123",
            [link_by_id("getUserById", userId="/users/(.+)")],
        ),
        # Absolute Location matching base_url - should strip base and work
        (
            "http://localhost:8080/api/v1",
            {"/users/{userId}": {"get": {"operationId": "getUserById"}}},
            "http://localhost:8080/api/v1/users/123",
            [link_by_id("getUserById", userId="/users/(.+)")],
        ),
        # Base URL without subpath
        (
            "http://localhost:8080",
            {"/users/{userId}": {"get": {"operationId": "getUserById"}}},
            "http://localhost:8080/users/123",
            [link_by_id("getUserById", userId="/users/(.+)")],
        ),
        # Absolute Location with different host - should not match
        (
            "http://localhost:8080/api",
            {"/users/{userId}": {"get": {"operationId": "getUserById"}}},
            "http://different-host.com/api/users/123",
            [],
        ),
        # Absolute Location with different base path - should not match
        (
            "http://localhost:8080/api/v1",
            {"/users/{userId}": {"get": {"operationId": "getUserById"}}},
            "http://localhost:8080/api/v2/users/123",
            [],
        ),
        # Partial matches work with absolute URLs
        (
            "http://localhost:8080/api",
            {
                "/users/{userId}": {"get": {"operationId": "getUser"}},
                "/users/{userId}/posts/{postId}": {"get": {"operationId": "getUserPost"}},
            },
            "http://localhost:8080/api/users/123",
            [
                link_by_id("getUser", userId="/users/(.+)"),
                link_by_id("getUserPost", userId="/users/(.+)"),
            ],
        ),
        # Base URL with trailing slash vs Location without
        (
            "http://localhost:8080/api/",
            {"/users/{userId}": {"get": {"operationId": "getUserById"}}},
            "http://localhost:8080/api/users/123",
            [link_by_id("getUserById", userId="/users/(.+)")],
        ),
        # Location with query parameters (should be ignored in matching)
        (
            "http://localhost:8080/api",
            {"/users/{userId}": {"get": {"operationId": "getUserById"}}},
            "http://localhost:8080/api/users/123?expand=profile",
            [link_by_id("getUserById", userId="/users/(.+)")],
        ),
        # Location with fragment (should be ignored in matching)
        (
            "http://localhost:8080/api",
            {"/users/{userId}": {"get": {"operationId": "getUserById"}}},
            "http://localhost:8080/api/users/123#profile",
            [link_by_id("getUserById", userId="/users/(.+)")],
        ),
        # Relative Location includes base path - should be stripped
        (
            "http://api.example.com/api/v1",
            {"/users/{userId}": {"get": {"operationId": "getUserById"}}},
            "/api/v1/users/123",
            [link_by_id("getUserById", userId="/users/(.+)")],
        ),
        # Multiple levels of base path
        (
            "http://api.example.com/api/v2/internal",
            {"/users/{userId}": {"get": {"operationId": "getUserById"}}},
            "/api/v2/internal/users/123",
            [link_by_id("getUserById", userId="/users/(.+)")],
        ),
        # Location has wrong base path - should not match
        (
            "http://api.example.com/api/v1",
            {"/users/{userId}": {"get": {"operationId": "getUserById"}}},
            "/api/v2/users/123",
            [],
        ),
        # Location has partial base path - should not match
        (
            "http://api.example.com/api/v1",
            {"/users/{userId}": {"get": {"operationId": "getUserById"}}},
            "/api/users/123",
            [],
        ),
        # Base path with special characters
        (
            "http://api.example.com/api-v1.0",
            {"/users/{userId}": {"get": {"operationId": "getUserById"}}},
            "/api-v1.0/users/123",
            [link_by_id("getUserById", userId="/users/(.+)")],
        ),
        # Complex prefix matching with base path
        (
            "http://api.example.com/v1",
            {
                "/users/{userId}": {"get": {"operationId": "getUser"}},
                "/users/{userId}/posts/{postId}": {"get": {"operationId": "getUserPost"}},
            },
            "/v1/users/123",
            [
                link_by_id("getUser", userId="/users/(.+)"),
                link_by_id("getUserPost", userId="/users/(.+)"),
            ],
        ),
        # Base path with trailing slash, Location without - should still work
        (
            "http://api.example.com/api/v1/",
            {"/users/{userId}": {"get": {"operationId": "getUserById"}}},
            "/api/v1/users/123",
            [link_by_id("getUserById", userId="/users/(.+)")],
        ),
    ],
)
def test_build_links_with_base_url(base_url, paths, location, expected, response_factory):
    schema = schemathesis.openapi.from_dict({"openapi": "3.1.0", "paths": paths})
    schema.config.base_url = base_url

    inferencer = LinkInferencer.from_schema(schema)
    results = build_links(inferencer, location)
    assert results == expected

    if results:
        assert_links_work(response_factory, location, results, schema)


@pytest.mark.parametrize(
    ["paths", "location", "expected"],
    [
        # Same path with multiple methods - ALL should be included
        (
            {
                "/users/{userId}": {
                    "get": {"operationId": "getUserById"},
                    "put": {"operationId": "updateUser"},
                    "delete": {"operationId": "deleteUser"},
                    "patch": {"operationId": "patchUser"},
                }
            },
            "/users/123",
            [
                link_by_id("getUserById", userId="/users/(.+)"),
                link_by_id("updateUser", userId="/users/(.+)"),
                link_by_id("deleteUser", userId="/users/(.+)"),
                link_by_id("patchUser", userId="/users/(.+)"),
            ],
        ),
        # Prefix matching should find ALL methods on matching paths
        (
            {
                "/users/{userId}": {
                    "get": {"operationId": "getUser"},
                    "put": {"operationId": "updateUser"},
                },
                "/users/{userId}/posts": {
                    "get": {"operationId": "getUserPosts"},
                    "post": {"operationId": "createUserPost"},
                },
                "/users/{userId}/posts/{postId}": {
                    "get": {"operationId": "getUserPost"},
                    "put": {"operationId": "updateUserPost"},
                    "delete": {"operationId": "deleteUserPost"},
                },
            },
            "/users/123",
            [
                link_by_id("getUser", userId="/users/(.+)"),
                link_by_id("updateUser", userId="/users/(.+)"),
                link_by_id("getUserPosts", userId="/users/(.+)"),
                link_by_id("createUserPost", userId="/users/(.+)"),
                link_by_id("getUserPost", userId="/users/(.+)"),
                link_by_id("updateUserPost", userId="/users/(.+)"),
                link_by_id("deleteUserPost", userId="/users/(.+)"),
            ],
        ),
    ],
)
def test_build_links_all_methods(paths, location, expected, response_factory):
    schema = schemathesis.openapi.from_dict({"openapi": "3.1.0", "paths": paths})
    inferencer = LinkInferencer.from_schema(schema)
    results = build_links(inferencer, location)
    assert results == expected

    if results:
        assert_links_work(response_factory, location, results, schema)


def test_build_links_no_paths_in_schema():
    # OpenAPI 3.1.0 allows schemas without paths
    schema = schemathesis.openapi.from_dict({"openapi": "3.1.0", "info": {"title": "Test", "version": "1.0"}})
    inferencer = LinkInferencer.from_schema(schema)
    assert build_links(inferencer, "/users/123") == []


def test_build_links_path_item_with_ref():
    raw_schema = {
        "openapi": "3.1.0",
        "info": {"title": "Test", "version": "1.0"},
        "paths": {
            "/users/{userId}": {
                "$ref": "#/components/pathItems/UserPathItem",
            }
        },
        "components": {
            "pathItems": {
                "UserPathItem": {
                    "get": {"operationId": "getUserById"},
                    "put": {"operationId": "updateUser"},
                    "delete": {"operationId": "deleteUser"},
                }
            }
        },
    }

    schema = schemathesis.openapi.from_dict(raw_schema)

    inferencer = LinkInferencer.from_schema(schema)
    assert build_links(inferencer, "/users/123") == [
        link_by_id("getUserById", userId="/users/(.+)"),
        link_by_id("updateUser", userId="/users/(.+)"),
        link_by_id("deleteUser", userId="/users/(.+)"),
    ]


def test_build_links_path_item_broken_ref():
    raw_schema = {
        "openapi": "3.1.0",
        "info": {"title": "Test", "version": "1.0"},
        "paths": {
            "/users/{userId}": {"$ref": "#/components/pathItems/NonExistentPathItem"},
            "/orders/{orderId}": {"get": {"operationId": "getOrder"}},
        },
    }

    schema = schemathesis.openapi.from_dict(raw_schema)
    inferencer = LinkInferencer.from_schema(schema)

    # Broken ref should not cause crashes, should just skip that path
    assert build_links(inferencer, "/orders/456") == [link_by_id("getOrder", orderId="/orders/(.+)")]

    # The broken ref path should not match anything
    assert build_links(inferencer, "/users/123") == []


def test_build_links_mixed_ref_and_inline_paths():
    raw_schema = {
        "openapi": "3.1.0",
        "info": {"title": "Test", "version": "1.0"},
        "paths": {
            "/users/{userId}": {"$ref": "#/components/pathItems/UserPathItem"},
            "/users/{userId}/posts": {
                "get": {"operationId": "getUserPosts"},
                "post": {"operationId": "createUserPost"},
            },
        },
        "components": {
            "pathItems": {"UserPathItem": {"get": {"operationId": "getUser"}, "put": {"operationId": "updateUser"}}}
        },
    }

    schema = schemathesis.openapi.from_dict(raw_schema)

    inferencer = LinkInferencer.from_schema(schema)
    assert build_links(inferencer, "/users/123") == [
        link_by_id("getUser", userId="/users/(.+)"),
        link_by_id("updateUser", userId="/users/(.+)"),
        link_by_id("getUserPosts", userId="/users/(.+)"),
        link_by_id("createUserPost", userId="/users/(.+)"),
    ]


def test_build_links_no_base_url_configured():
    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.1.0",
            "paths": {"/users/{userId}": {"get": {"operationId": "getUserById"}, "put": {"operationId": "updateUser"}}},
        }
    )
    assert schema.config.base_url is None

    inferencer = LinkInferencer.from_schema(schema)

    # Relative Location should work fine
    assert build_links(inferencer, "/users/123") == [
        link_by_id("getUserById", userId="/users/(.+)"),
        link_by_id("updateUser", userId="/users/(.+)"),
    ]

    # Absolute Location should be ignored (can't validate without base_url)
    assert build_links(inferencer, "http://api.example.com/users/123") == []

    # Different absolute URLs should also be ignored
    assert build_links(inferencer, "https://localhost:8080/api/v1/users/123") == []

    # Another relative path should work
    assert build_links(inferencer, "/users/456") == [
        link_by_id("getUserById", userId="/users/(.+)"),
        link_by_id("updateUser", userId="/users/(.+)"),
    ]


@pytest.fixture
def user_api_schema(ctx):
    return ctx.openapi.build_schema(
        {
            "/users": {
                "post": {
                    "operationId": "create_user",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        }
                    },
                    "responses": {
                        "201": {
                            "description": "User created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "integer"}},
                                        "required": ["id"],
                                    }
                                }
                            },
                        },
                        "default": {"description": "Error"},
                    },
                }
            },
            "/users/{userId}": {
                "get": {
                    "operationId": "get_user",
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {
                        "200": {
                            "description": "User found",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                                        "required": ["id", "name"],
                                    }
                                }
                            },
                        },
                        "default": {"description": "Error"},
                    },
                },
                "put": {
                    "operationId": "update_user",
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        }
                    },
                    "responses": {"204": {"description": "User updated"}, "404": {"description": "User not found"}},
                },
            },
        }
    )


@pytest.fixture
def user_api_app(user_api_schema):
    app = Flask(__name__)
    users = {}
    next_id = 1

    @app.route("/openapi.json")
    def schema():
        return jsonify(user_api_schema)

    @app.route("/users", methods=["POST"])
    def create_user():
        nonlocal next_id
        data = request.get_json()
        if not isinstance(data, dict):
            return {"error": "Invalid input"}
        user_id = next_id
        next_id += 1

        users[user_id] = {"id": user_id, "name": str(data.get("name", "DefaultName")), "corrupted": False}

        return jsonify({"id": user_id}), 201, {"Location": f"/users/{user_id}"}

    @app.route("/users/<int:user_id>", methods=["GET"])
    def get_user(user_id):
        if user_id not in users:
            return "", 404

        user = users[user_id]

        # Bug: return invalid schema when corrupted
        if user.get("corrupted"):
            return jsonify(
                {
                    "id": 42,
                    # Should be a string
                    "name": None,
                }
            ), 200

        return jsonify({"id": user_id, "name": user["name"]}), 200

    @app.route("/users/<int:user_id>", methods=["PUT"])
    def update_user(user_id):
        if user_id not in users:
            return "", 404

        # Bug: PUT corrupts data, breaking subsequent GET calls
        users[user_id]["corrupted"] = True

        return "", 204

    return app


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_link_inference_discovers_corruption_bug(cli, app_runner, snapshot_cli, user_api_app):
    port = app_runner.run_flask_app(user_api_app)
    assert (
        cli.run(
            "--max-examples=10",
            "-c response_schema_conformance",
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing,stateful",
            config={"phases": {"stateful": {"inference": {"algorithms": ["location-headers"]}}}},
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_link_inference_accounts_for_filters(cli, app_runner, snapshot_cli, user_api_app):
    port = app_runner.run_flask_app(user_api_app)
    assert (
        cli.run(
            "--max-examples=10",
            "-c response_schema_conformance",
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing,stateful",
            "--include-method=POST",
        )
        == snapshot_cli
    )


def test_stateful_disabled_skips_link_inference(cli, app_runner, snapshot_cli, user_api_app):
    port = app_runner.run_flask_app(user_api_app)
    assert (
        cli.run(
            "--max-examples=10",
            "-c response_schema_conformance",
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing",
            config={"warnings": False},
        )
        == snapshot_cli
    )


def test_inference_disabled_via_config(cli, app_runner, snapshot_cli, user_api_app):
    port = app_runner.run_flask_app(user_api_app)
    assert (
        cli.run(
            "--max-examples=10",
            "-c response_schema_conformance",
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing,stateful",
            config={"phases": {"stateful": {"inference": {"algorithms": []}}}, "warnings": False},
        )
        == snapshot_cli
    )


def test_location_points_to_nonexistent_endpoint(cli, app_runner, snapshot_cli, ctx):
    schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "operationId": "create_item",
                    "responses": {"201": {"description": "Item created"}},
                }
            },
            # Note: No /items/{itemId} endpoint defined
        }
    )

    app = Flask(__name__)

    @app.route("/openapi.json")
    def get_schema():
        return jsonify(schema)

    @app.route("/items", methods=["POST"])
    def create_item():
        # Returns Location pointing to endpoint not defined in schema
        return jsonify({"message": "created"}), 201, {"Location": "/items/123"}

    port = app_runner.run_flask_app(app)
    assert (
        cli.run(
            "--max-examples=5",
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing,stateful",
        )
        == snapshot_cli
    )


def test_inject_links_location_normalization_returns_none():
    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.1.0",
            "paths": {
                "/users": {
                    "post": {
                        "operationId": "createUser",
                        "responses": {
                            "201": {},
                        },
                    }
                }
            },
        }
    )
    inferencer = LinkInferencer.from_schema(schema)
    operation = schema["/users"]["post"]

    # Empty location should be normalized to None
    entries = [LocationHeaderEntry(value="", status_code=201)]

    assert inferencer.inject_links(operation.responses, entries) == 0
    response = operation.responses.find_by_status_code("201")
    assert "links" not in response.definition


def test_inject_links_no_matches():
    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.1.0",
            "paths": {
                "/users": {
                    "post": {
                        "operationId": "createUser",
                        "responses": {
                            "201": {},
                        },
                    }
                }
            },
        }
    )
    inferencer = LinkInferencer.from_schema(schema)
    operation = schema["/users"]["post"]

    # Location that doesn't match any endpoint
    entries = [LocationHeaderEntry(value="/orders/123", status_code=201)]

    assert inferencer.inject_links(operation.responses, entries) == 0
    response = operation.responses.find_by_status_code("201")
    assert "links" not in response.definition


def test_inject_links_creates_response_definition():
    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.1.0",
            "paths": {
                "/users": {"post": {"operationId": "createUser"}},
                "/users/{userId}": {"get": {"operationId": "getUser"}},
            },
        }
    )
    inferencer = LinkInferencer.from_schema(schema)
    # No 201 response defined
    operation = schema["/users"]["post"]

    entries = [LocationHeaderEntry(value="/users/123", status_code=201)]

    assert inferencer.inject_links(operation.responses, entries) == 1
    # Should create the 201 response definition
    assert "201" in operation.responses.status_codes
    response = operation.responses.find_by_status_code("201")
    assert "links" in response.definition
    assert "X-Inferred-Link-0" in response.definition["links"]
