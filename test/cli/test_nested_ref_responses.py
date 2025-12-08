import pytest
from flask import Flask, jsonify


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_bundled_ref_in_error_message(ctx, app_runner, cli, snapshot_cli):
    # When a response schema has array items with $ref, the bundled ref path like `#/x-bundled/schema1`
    # should not appear in error messages - it should show the original reference path instead
    raw_schema = ctx.openapi.build_schema(
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

    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

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
    raw_schema = ctx.openapi.build_schema(
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

    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/api/users", methods=["GET"])
    def get_users():
        # Violates schema: missing "name", wrong type for "id"
        return jsonify({"id": "not-an-integer", "email": "test@example.com"}), 200

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
