import pytest
from flask import Response

import schemathesis
from schemathesis.openapi.checks import JsonSchemaError


def _response(media_type, schema):
    return {"get": {"responses": {"200": {"description": "OK", "content": {media_type: {"schema": schema}}}}}}


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_custom_deserializers(ctx, cli, snapshot_cli):
    raw_schema = ctx.openapi.build_schema(
        {
            "/ok": _response(
                "application/vnd.ok",
                {
                    "type": "object",
                    "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                    "required": ["key", "value"],
                },
            ),
            "/broken": _response(
                "application/vnd.broken",
                {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
            ),
            "/mismatch": _response(
                "application/vnd.lines",
                {
                    "type": "object",
                    "properties": {"count": {"type": "integer"}, "name": {"type": "string"}},
                    "required": ["count", "name"],
                },
            ),
            "/image": _response("image/png", {"type": "string", "format": "binary"}),
            "/multi": _response(
                "application/vnd.multi",
                {
                    "type": "object",
                    "properties": {"parsed_by": {"type": "string", "enum": ["second"]}},
                    "required": ["parsed_by"],
                },
            ),
        },
        version="3.0.0",
    )
    app = ctx.openapi.make_flask_app_from_schema(raw_schema)
    bodies = {
        "ok": ("name=Alice", "application/vnd.ok"),
        "broken": ("malformed_data", "application/vnd.broken"),
        "mismatch": ("count=notanumber\nname=Alice", "application/vnd.lines"),
        "image": (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00", "image/png"),
        "multi": ("test", "application/vnd.multi"),
    }

    @app.route("/<name>", methods=["GET"])
    def endpoint(name):
        content, content_type = bodies[name]
        return Response(content, content_type=content_type)

    hooks_module = ctx.write_pymodule("""
@schemathesis.deserializer("application/vnd.ok")
def deserialize_ok(ctx, response):
    text = response.content.decode("utf-8")
    parts = text.split("=", 1)
    if len(parts) == 2:
        return {"key": parts[0], "value": parts[1]}
    raise ValueError("Invalid format")

@schemathesis.deserializer("application/vnd.broken")
def deserialize_broken(ctx, response):
    text = response.content.decode("utf-8")
    if "=" not in text:
        raise ValueError(f"Invalid custom format: expected 'key=value', got '{text}'")
    key, value = text.split("=", 1)
    return {"key": key, "value": value}

@schemathesis.deserializer("application/vnd.lines")
def deserialize_lines(ctx, response):
    text = response.content.decode("utf-8")
    result = {}
    for line in text.split("\\n"):
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result

@schemathesis.deserializer("application/vnd.multi")
def deserialize_first(ctx, response):
    return {"parsed_by": "first"}

@schemathesis.deserializer("application/vnd.multi")
def deserialize_second(ctx, response):
    return {"parsed_by": "second"}
""")

    assert cli.run_openapi_app(app, "--checks=response_schema_conformance", hooks=hooks_module) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_deserializer_with_wildcard_media_type(ctx, cli, snapshot_cli):
    raw_schema = ctx.openapi.build_schema(
        {
            "/data1": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/vnd.api+custom": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "string"}}}
                                }
                            },
                        }
                    }
                }
            },
            "/data2": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/vnd.other+custom": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "string"}}}
                                }
                            },
                        }
                    }
                }
            },
        },
        version="3.0.0",
    )

    app = ctx.openapi.make_flask_app_from_schema(raw_schema)

    @app.route("/data1", methods=["GET"])
    def get_data1():
        return Response("1", content_type="application/vnd.api+custom")

    @app.route("/data2", methods=["GET"])
    def get_data2():
        return Response("2", content_type="application/vnd.other+custom")

    # Register deserializer with wildcard - should match both endpoints
    hooks_module = ctx.write_pymodule("""
@schemathesis.deserializer("application/vnd.*+custom")
def deserialize_custom(ctx, response):
    return {"id": response.content.decode("utf-8")}
""")

    assert cli.run_openapi_app(app, "--checks=response_schema_conformance", hooks=hooks_module) == snapshot_cli


def test_wildcard_deserializer_matches_concrete_media_type(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/msgpack": {"schema": {"type": "integer"}}},
                        }
                    }
                }
            }
        }
    )

    @schemathesis.deserializer("application/*")
    def deserialize_any_application(ctx, response):
        return response.content.decode()

    response = response_factory.requests(content=b"not-an-integer", content_type="application/msgpack")
    with pytest.raises(JsonSchemaError, match='"not-an-integer" is not of type "integer"'):
        schema["/data"]["GET"].validate_response(response)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_deserializer_error_in_stateful_phase(ctx, cli, snapshot_cli):
    # Without `response_schema_conformance` the deserializer first runs between stateful suites.
    custom = {"application/vnd.custom": {"schema": {"type": "object"}}}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": custom,
                            "links": {"GetItem": {"operationId": "getItem", "parameters": {"itemId": 1}}},
                        }
                    },
                }
            },
            "/items/{itemId}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK", "content": custom}},
                }
            },
        }
    )

    @app.route("/items", methods=["POST"])
    def create_item():
        return Response("{}", status=201, content_type="application/vnd.custom")

    @app.route("/items/<int:item_id>", methods=["GET"])
    def get_item(item_id):
        return Response("{}", content_type="application/vnd.custom")

    hooks_module = ctx.write_pymodule("""
@schemathesis.deserializer("application/vnd.custom")
def deserialize_custom(ctx, response):
    raise RuntimeError("Broken deserializer")
""")

    assert (
        cli.run_openapi_app(
            app, "--phases=stateful", "--checks=not_a_server_error", "--max-examples=5", hooks=hooks_module
        )
        == snapshot_cli
    )
