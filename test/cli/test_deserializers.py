import pytest
from flask import Flask, Response, jsonify


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_custom_deserializer_successful(ctx, app_runner, cli, snapshot_cli):
    raw_schema = {
        "openapi": "3.0.0",
        "paths": {
            "/data": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/vnd.custom": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                                        "required": ["key", "value"],
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
    }

    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/data", methods=["GET"])
    def get_data():
        return Response("name=Alice", content_type="application/vnd.custom")

    port = app_runner.run_flask_app(app)

    hooks_module = ctx.write_pymodule("""
@schemathesis.deserializer("application/vnd.custom")
def deserialize_custom(ctx, response):
    text = response.content.decode("utf-8")
    parts = text.split("=", 1)
    if len(parts) == 2:
        return {"key": parts[0], "value": parts[1]}
    raise ValueError("Invalid format")
""")

    assert (
        cli.run(f"http://127.0.0.1:{port}/openapi.json", "--checks=response_schema_conformance", hooks=hooks_module)
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_custom_deserializer_with_exception(ctx, app_runner, cli, snapshot_cli):
    raw_schema = {
        "openapi": "3.0.0",
        "paths": {
            "/data": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/vnd.custom": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"key": {"type": "string"}},
                                        "required": ["key"],
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
    }

    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/data", methods=["GET"])
    def get_data():
        return Response("malformed_data", content_type="application/vnd.custom")

    port = app_runner.run_flask_app(app)

    hooks_module = ctx.write_pymodule("""
@schemathesis.deserializer("application/vnd.custom")
def deserialize_custom(ctx, response):
    text = response.content.decode("utf-8")
    if "=" not in text:
        raise ValueError(f"Invalid custom format: expected 'key=value', got '{text}'")
    key, value = text.split("=", 1)
    return {"key": key, "value": value}
""")

    assert (
        cli.run(f"http://127.0.0.1:{port}/openapi.json", "--checks=response_schema_conformance", hooks=hooks_module)
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_unsupported_media_type_silent_skip(app_runner, cli, snapshot_cli):
    raw_schema = {
        "openapi": "3.0.0",
        "paths": {
            "/image": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
                        }
                    }
                }
            }
        },
    }

    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/image", methods=["GET"])
    def get_image():
        fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00"
        return Response(fake_png, content_type="image/png")

    port = app_runner.run_flask_app(app)

    assert cli.run(f"http://127.0.0.1:{port}/openapi.json", "--checks=response_schema_conformance") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_custom_deserializer_schema_mismatch(ctx, app_runner, cli, snapshot_cli):
    raw_schema = {
        "openapi": "3.0.0",
        "paths": {
            "/data": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/vnd.custom": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"count": {"type": "integer"}, "name": {"type": "string"}},
                                        "required": ["count", "name"],
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
    }

    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/data", methods=["GET"])
    def get_data():
        return Response("count=notanumber\nname=Alice", content_type="application/vnd.custom")

    port = app_runner.run_flask_app(app)

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

    assert (
        cli.run(f"http://127.0.0.1:{port}/openapi.json", "--checks=response_schema_conformance", hooks=hooks_module)
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_multiple_deserializers_for_same_type(ctx, app_runner, cli, snapshot_cli):
    raw_schema = {
        "openapi": "3.0.0",
        "paths": {
            "/data": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/vnd.custom": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"parsed_by": {"type": "string", "enum": ["second"]}},
                                        "required": ["parsed_by"],
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
    }

    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/data", methods=["GET"])
    def get_data():
        return Response("test", content_type="application/vnd.custom")

    port = app_runner.run_flask_app(app)

    hooks_module = ctx.write_pymodule("""
@schemathesis.deserializer("application/vnd.custom")
def deserialize_first(ctx, response):
    return {"parsed_by": "first"}

@schemathesis.deserializer("application/vnd.custom")
def deserialize_second(ctx, response):
    return {"parsed_by": "second"}
""")

    assert (
        cli.run(f"http://127.0.0.1:{port}/openapi.json", "--checks=response_schema_conformance", hooks=hooks_module)
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_deserializer_with_wildcard_media_type(ctx, app_runner, cli, snapshot_cli):
    """Deserializer with wildcard pattern matches multiple media types."""
    raw_schema = {
        "openapi": "3.0.0",
        "paths": {
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
    }

    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/data1", methods=["GET"])
    def get_data1():
        return Response("1", content_type="application/vnd.api+custom")

    @app.route("/data2", methods=["GET"])
    def get_data2():
        return Response("2", content_type="application/vnd.other+custom")

    port = app_runner.run_flask_app(app)

    # Register deserializer with wildcard - should match both endpoints
    hooks_module = ctx.write_pymodule("""
@schemathesis.deserializer("application/vnd.*+custom")
def deserialize_custom(ctx, response):
    return {"id": response.content.decode("utf-8")}
""")

    assert (
        cli.run(f"http://127.0.0.1:{port}/openapi.json", "--checks=response_schema_conformance", hooks=hooks_module)
        == snapshot_cli
    )
