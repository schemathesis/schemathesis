import json

import pytest
from _pytest.main import ExitCode
from flask import Flask, jsonify, request


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_format_password_false_positive(ctx, app_runner, cli, snapshot_cli):
    # GH-3480: Schemathesis incorrectly reports data as invalid for format: password
    # In OpenAPI 3.0, `format` is an annotation and does NOT impose validation constraints by itself.
    # With only `type: string, format: password`, any string is valid.
    # Schemathesis incorrectly treats generated data as schema-violating and expects rejection.
    #
    # The coverage phase generates format-specific negative cases via _negative_format,
    # which incorrectly claims values don't match the 'password' format.
    raw_schema = ctx.openapi.build_schema(
        {
            "/": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "string",
                                    "format": "password",
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/", methods=["POST"])
    def root():
        # Reject non-strings with 422 (proper validation behavior)
        # Accept all strings (including empty) since format:password has no validation semantics
        data = request.get_data()
        try:
            body = json.loads(data)
            if not isinstance(body, str):
                return jsonify({"error": "expected string"}), 422
        except (json.JSONDecodeError, ValueError):
            return jsonify({"error": "invalid json"}), 422
        return jsonify({"result": "ok"}), 200

    port = app_runner.run_flask_app(app)

    # No failure should be reported since any string is valid for format: password in OpenAPI 3.0
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--checks=negative_data_rejection",
            "--phases=coverage",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


def test_negative_metadata_required_property(ctx, app_runner, cli):
    raw_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["name", "description"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/items", methods=["POST"])
    def create_item():
        return jsonify({"result": "ok"}), 200

    port = app_runner.run_flask_app(app)

    result = cli.run_and_assert(
        f"http://127.0.0.1:{port}/openapi.json",
        "--checks=negative_data_rejection",
        "--mode=negative",
        "--phases=fuzzing",
        "--max-examples=25",
        "--continue-on-failure",
        exit_code=ExitCode.TESTS_FAILED,
    )
    assert "API accepted schema-violating request" in result.stdout
    assert "Invalid component: in body" in result.stdout


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_text_plain_negative_becomes_valid_after_serialization(ctx, app_runner, cli, snapshot_cli):
    raw_schema = ctx.openapi.build_schema(
        {
            "/some-string-endpoint": {
                "put": {
                    "requestBody": {
                        "required": True,
                        "content": {"text/plain": {"schema": {"type": "string"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/some-string-endpoint", methods=["PUT"])
    def string_endpoint():
        return "", 200

    port = app_runner.run_flask_app(app)

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--checks=negative_data_rejection",
            "--mode=negative",
            "--phases=fuzzing",
            "--max-examples=5",
            "--seed=1",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_text_plain_with_query_negative_still_fails(ctx, app_runner, cli, snapshot_cli):
    raw_schema = ctx.openapi.build_schema(
        {
            "/endpoint": {
                "put": {
                    "parameters": [
                        {"in": "query", "name": "age", "required": True, "schema": {"type": "integer", "minimum": 0}}
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {"text/plain": {"schema": {"type": "string"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/endpoint", methods=["PUT"])
    def endpoint():
        return "", 200

    port = app_runner.run_flask_app(app)

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--checks=negative_data_rejection",
            "--mode=negative",
            "--phases=fuzzing",
            "--max-examples=10",
            "--seed=3",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    "auth_config",
    [
        {"auth": {"openapi": {"password": {"username": "plain", "password": "test"}}}},
        {"auth": {"basic": {"username": "plain", "password": "test"}}},
        None,
    ],
    ids=["openapi-auth", "basic-auth", "no-auth"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_removed_auth_parameter_not_reapplied(ctx, app_runner, cli, snapshot_cli, auth_config):
    raw_schema = ctx.openapi.build_schema(
        {
            "/ping": {
                "post": {
                    "security": [{"password": []}],
                    "responses": {"204": {"description": "No Content"}},
                }
            }
        },
        components={
            "securitySchemes": {
                "password": {
                    "type": "http",
                    "scheme": "basic",
                }
            }
        },
    )

    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/ping", methods=["POST"])
    def ping():
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return "", 401
        return "", 204

    port = app_runner.run_flask_app(app)

    args = [
        f"http://127.0.0.1:{port}/openapi.json",
        "--checks=negative_data_rejection",
        "--mode=negative",
        "--phases=fuzzing",
        "--max-examples=5",
        "--seed=42",
    ]
    kwargs = {"config": auth_config} if auth_config else {}

    assert cli.run(*args, **kwargs) == snapshot_cli
