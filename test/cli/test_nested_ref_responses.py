import pytest
from flask import jsonify


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_bundled_ref_schema_path_display(ctx, app_runner, cli, snapshot_cli):
    # When response validation fails inside a $ref-ed component, the "Schema at" path
    # should show the original component path (e.g. /components/schemas/Host/properties/host)
    # not the internal bundled form (e.g. /x-bundled/schema1/properties/host).
    # See GH-3567
    app, _ = ctx.openapi.make_flask_app(
        {
            "/search": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Search result",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"host": {"$ref": "#/components/schemas/HostField"}},
                                        "required": ["host"],
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
        version="3.0.2",
        components={
            "schemas": {
                "HostField": {
                    "type": "string",
                }
            }
        },
    )

    @app.route("/search", methods=["GET"])
    def search():
        # Return an integer for host instead of a string — always a type error
        return jsonify({"host": 42}), 200

    port = app_runner.run_flask_app(app)

    result = cli.run(
        f"http://127.0.0.1:{port}/openapi.json",
        "--checks=response_schema_conformance",
        "--max-examples=1",
    )
    assert "x-bundled" not in result.stdout
    assert result == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_bundled_ref_in_error_message(ctx, app_runner, cli, snapshot_cli):
    # When a response schema has array items with $ref, the bundled ref path like `#/x-bundled/schema1`
    # should not appear in error messages - it should show the original reference path instead
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "List of items",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/Item"},
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
        version="3.0.2",
        components={
            "schemas": {
                "Item": {
                    "type": "object",
                    "required": ["id", "name"],
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                    },
                }
            }
        },
    )

    @app.route("/items", methods=["GET"])
    def get_items():
        # Returns a string instead of array - triggers type mismatch error
        # The error message should show `#/components/schemas/Item` instead of `#/x-bundled/schema1`
        return jsonify("not an array"), 200

    port = app_runner.run_flask_app(app)

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--checks=response_schema_conformance",
            "--max-examples=1",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_nested_ref_in_response_definition(ctx, app_runner, cli, snapshot_cli):
    # Response 200 -> #/definitions/UserResponse -> actual response with schema
    # Without fix: nested $ref won't be resolved, schema validation won't work
    app, _ = ctx.openapi.make_flask_app(
        {"/users": {"get": {"responses": {"200": {"$ref": "#/definitions/UserResponse"}}}}},
        version="2.0",
        definitions={
            "UserResponse": {"$ref": "#/definitions/UserResponseDef"},
            "UserResponseDef": {
                "description": "User data",
                "schema": {
                    "type": "object",
                    "required": ["id", "name"],
                    "properties": {"id": {"type": "integer"}, "name": {"type": "string"}, "email": {"type": "string"}},
                },
            },
        },
    )

    @app.route("/api/users", methods=["GET"])
    def get_users():
        # Violates schema: missing "name", wrong type for "id"
        return jsonify({"id": "not-an-integer", "email": "test@example.com", "name": "test"}), 200

    port = app_runner.run_flask_app(app)

    # Should detect schema violations via response_schema_conformance
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--checks=response_schema_conformance",
            "--max-examples=1",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_bundled_ref_in_negative_testing_description(ctx, app_runner, cli, snapshot_cli):
    # When a request body schema has $ref with multiple definitions, the negative testing (fuzzing)
    # phase may negate the $ref constraint. The error description should show the original reference
    # path (e.g., `#/components/schemas/Item`) instead of the internal bundled path (e.g., `#/x-bundled/schema1`).
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "allOf": [
                                        {"$ref": "#/components/schemas/Item"},
                                        {"$ref": "#/components/schemas/Category"},
                                    ]
                                }
                            }
                        },
                    },
                    "responses": {"201": {"description": "Created"}},
                }
            }
        },
        version="3.0.2",
        components={
            "schemas": {
                "Item": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "integer"},
                    },
                },
                "Category": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string"},
                    },
                },
            }
        },
    )

    @app.route("/items", methods=["POST"])
    def create_item():
        # Accept all requests to trigger negative_data_rejection failures
        return jsonify({"id": 1}), 201

    port = app_runner.run_flask_app(app)

    result = cli.run(
        f"http://127.0.0.1:{port}/openapi.json",
        "--checks=negative_data_rejection",
        "--phases=fuzzing",
        "--mode=negative",
        "--max-examples=10",
    )

    assert "#/x-bundled/" not in result.stdout

    assert result == snapshot_cli
