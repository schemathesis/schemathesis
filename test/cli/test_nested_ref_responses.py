import pytest
from flask import jsonify, request


@pytest.mark.parametrize(
    ["reference", "component"],
    [
        (
            "https://example.invalid/text.json",
            {"$id": "https://example.invalid/text.json", "type": "string"},
        ),
        (
            "https://example.invalid/text.json#/foo",
            {"$id": "https://example.invalid/text.json", "foo": {"type": "string"}},
        ),
    ],
    ids=["whole-document", "non-component-fragment"],
)
def test_external_ref_in_failure_schema_resolves(ctx, cli, snapshot_cli, reference, component):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/data": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["a"],
                                        "properties": {"a": {"$ref": reference}},
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
        version="3.1.0",
        components={"schemas": {"Text": component}},
    )

    @app.route("/data", methods=["GET"])
    def data():
        return jsonify({})

    assert (
        cli.run_openapi_app(
            app,
            "--max-examples=1",
            "--checks=response_schema_conformance",
            "--phases=examples,fuzzing",
        )
        == snapshot_cli
    )


def test_bundled_ref_schema_path_display(ctx, cli, snapshot_cli):
    # GH-3567: "Schema at" shows the component path, not the internal bundled `/x-bundled/...` form.
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
        return jsonify({"host": 42}), 200

    assert (
        cli.run_openapi_app(
            app,
            "--checks=response_schema_conformance",
            "--max-examples=1",
        )
        == snapshot_cli
    )


def test_bundled_ref_in_error_message(ctx, cli, snapshot_cli):
    # Errors for `$ref` array items name the original reference, not the bundled `#/x-bundled/schema1`.
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
        return jsonify("not an array"), 200

    assert (
        cli.run_openapi_app(
            app,
            "--checks=response_schema_conformance",
            "--max-examples=1",
        )
        == snapshot_cli
    )


def test_nested_ref_in_response_definition(ctx, cli, snapshot_cli):
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
        return jsonify({"id": "not-an-integer", "email": "test@example.com", "name": "test"}), 200

    assert (
        cli.run_openapi_app(
            app,
            "--checks=response_schema_conformance",
            "--max-examples=1",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_invalid_component=True)
def test_bundled_ref_in_negative_testing_description(ctx, cli, snapshot_cli):
    # A negated `$ref` body is described by the original reference, not the bundled `#/x-bundled/schema1`.
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
        body = request.get_data()
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return jsonify({"error": "non-printable"}), 400
        if any(ord(char) < 32 or ord(char) == 127 for char in text):
            return jsonify({"error": "non-printable"}), 400
        return jsonify({"id": 1}), 201

    assert (
        cli.run_openapi_app(
            app,
            "--checks=negative_data_rejection",
            "--phases=fuzzing",
            "--mode=negative",
            "--max-examples=10",
        )
        == snapshot_cli
    )


def test_whole_external_file_reference_in_schema_location(ctx, cli, app_runner, snapshot_cli):
    # A `$ref` without a fragment targets the whole file, which "Schema at" must name.
    paths = {
        "/pets": {
            "get": {
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "pets.json"}}}},
                    }
                }
            }
        }
    }
    app = ctx.openapi.make_permissive_flask_app(ctx.openapi.build_schema(paths, version="3.0.2"))
    base_url = app_runner.openapi_url(app, path="")
    schema_path = ctx.openapi.write_schema(paths, version="3.0.2")
    ctx.makefile({"type": "object", "required": ["name"]}, filename="pets")

    @app.route("/api/pets", methods=["GET"])
    def pets():
        # Missing `name` violates the referenced schema.
        return jsonify([{"x": 1}]), 200

    assert (
        cli.run(
            str(schema_path),
            f"--url={base_url}/api",
            "--checks=response_schema_conformance",
            "--max-examples=1",
        )
        == snapshot_cli
    )
