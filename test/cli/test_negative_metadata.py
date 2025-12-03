import pytest
from _pytest.main import ExitCode
from flask import Flask, jsonify, request


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
