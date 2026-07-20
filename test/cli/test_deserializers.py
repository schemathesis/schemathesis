import pytest
from flask import Response


def _schema(ctx, path, media_type, schema):
    return ctx.openapi.build_schema(
        {path: {"get": {"responses": {"200": {"description": "OK", "content": {media_type: {"schema": schema}}}}}}},
        version="3.0.0",
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_custom_deserializer_successful(ctx, cli, snapshot_cli):
    raw_schema = _schema(
        ctx,
        "/data",
        "application/vnd.custom",
        {
            "type": "object",
            "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
            "required": ["key", "value"],
        },
    )

    app = ctx.openapi.make_flask_app_from_schema(raw_schema)

    @app.route("/data", methods=["GET"])
    def get_data():
        return Response("name=Alice", content_type="application/vnd.custom")

    hooks_module = ctx.write_pymodule("""
@schemathesis.deserializer("application/vnd.custom")
def deserialize_custom(ctx, response):
    text = response.content.decode("utf-8")
    parts = text.split("=", 1)
    if len(parts) == 2:
        return {"key": parts[0], "value": parts[1]}
    raise ValueError("Invalid format")
""")

    assert cli.run_openapi_app(app, "--checks=response_schema_conformance", hooks=hooks_module) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_custom_deserializer_with_exception(ctx, cli, snapshot_cli):
    raw_schema = _schema(
        ctx,
        "/data",
        "application/vnd.custom",
        {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
    )

    app = ctx.openapi.make_flask_app_from_schema(raw_schema)

    @app.route("/data", methods=["GET"])
    def get_data():
        return Response("malformed_data", content_type="application/vnd.custom")

    hooks_module = ctx.write_pymodule("""
@schemathesis.deserializer("application/vnd.custom")
def deserialize_custom(ctx, response):
    text = response.content.decode("utf-8")
    if "=" not in text:
        raise ValueError(f"Invalid custom format: expected 'key=value', got '{text}'")
    key, value = text.split("=", 1)
    return {"key": key, "value": value}
""")

    assert cli.run_openapi_app(app, "--checks=response_schema_conformance", hooks=hooks_module) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_unsupported_media_type_silent_skip(ctx, cli, snapshot_cli):
    raw_schema = _schema(ctx, "/image", "image/png", {"type": "string", "format": "binary"})

    app = ctx.openapi.make_flask_app_from_schema(raw_schema)

    @app.route("/image", methods=["GET"])
    def get_image():
        fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00"
        return Response(fake_png, content_type="image/png")

    assert cli.run_openapi_app(app, "--checks=response_schema_conformance") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_custom_deserializer_schema_mismatch(ctx, cli, snapshot_cli):
    raw_schema = _schema(
        ctx,
        "/data",
        "application/vnd.custom",
        {
            "type": "object",
            "properties": {"count": {"type": "integer"}, "name": {"type": "string"}},
            "required": ["count", "name"],
        },
    )

    app = ctx.openapi.make_flask_app_from_schema(raw_schema)

    @app.route("/data", methods=["GET"])
    def get_data():
        return Response("count=notanumber\nname=Alice", content_type="application/vnd.custom")

    hooks_module = ctx.write_pymodule("""
@schemathesis.deserializer("application/vnd.custom")
def deserialize_custom(ctx, response):
    text = response.content.decode("utf-8")
    result = {}
    for line in text.split("\\n"):
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result
""")

    assert cli.run_openapi_app(app, "--checks=response_schema_conformance", hooks=hooks_module) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_multiple_deserializers_for_same_type(ctx, cli, snapshot_cli):
    raw_schema = _schema(
        ctx,
        "/data",
        "application/vnd.custom",
        {
            "type": "object",
            "properties": {"parsed_by": {"type": "string", "enum": ["second"]}},
            "required": ["parsed_by"],
        },
    )

    app = ctx.openapi.make_flask_app_from_schema(raw_schema)

    @app.route("/data", methods=["GET"])
    def get_data():
        return Response("test", content_type="application/vnd.custom")

    hooks_module = ctx.write_pymodule("""
@schemathesis.deserializer("application/vnd.custom")
def deserialize_first(ctx, response):
    return {"parsed_by": "first"}

@schemathesis.deserializer("application/vnd.custom")
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
