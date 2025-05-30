import pytest
from _pytest.main import ExitCode
from flask import Flask, jsonify, request

import schemathesis
from schemathesis.checks import CHECKS


@pytest.fixture
def new_check():
    @schemathesis.check
    def check_function(ctx, response, case):
        pass

    yield check_function

    CHECKS.unregister(check_function.__name__)


def test_register_returns_a_value(new_check):
    # When a function is registered via the `schemathesis.check` decorator
    # Then this function should be available for further usage
    # See #721
    assert new_check is not None


def test_negative_data_rejection(ctx, cli, openapi3_base_url):
    schema_path = ctx.openapi.write_schema(
        {
            "/success": {
                "get": {
                    "parameters": [{"name": "key", "in": "query", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    result = cli.run(
        str(schema_path),
        f"--url={openapi3_base_url}",
        "--checks",
        "negative_data_rejection",
        "--mode",
        "negative",
        "--max-examples=5",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_negative_data_rejection_displays_all_cases(app_runner, cli, snapshot_cli):
    raw_schema = {
        "openapi": "3.0.0",
        "paths": {
            "/test": {
                "get": {
                    "parameters": [
                        {
                            "name": "Accept-Language",
                            "in": "header",
                            "required": True,
                            "schema": {
                                "type": "string",
                                "enum": ["en-US", "fr-FR"],
                            },
                        },
                        {
                            "name": "$lang",
                            "in": "query",
                            "required": False,
                            "schema": {
                                "type": "string",
                                "enum": ["ro-RO", "th-TH"],
                                "example": "en-US",
                            },
                        },
                    ],
                    "responses": {
                        "default": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"message": {"type": "string"}},
                                        "required": ["message"],
                                    },
                                }
                            },
                        }
                    },
                }
            }
        },
    }
    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/test", methods=["GET"])
    def test_endpoint():
        header = request.headers.get("Accept-Language")
        if header not in ["en-US", "fr-FR"]:
            return jsonify({"message": "negative"}), 406
        return jsonify({"incorrect": "positive"}), 200

    port = app_runner.run_flask_app(app)

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--mode=all",
            "--phases=coverage",
            "--continue-on-failure",
            config={
                "checks": {
                    "negative_data_rejection": {"expected-statuses": ["400", "401", "403", "404", "422", "428", "5xx"]}
                }
            },
        )
        == snapshot_cli
    )


def test_format_parameter_csv_response(app_runner, cli, snapshot_cli):
    raw_schema = {
        "openapi": "3.0.0",
        "paths": {
            "/data": {
                "get": {
                    "parameters": [
                        {
                            "name": "format",
                            "in": "query",
                            "schema": {
                                "type": "string",
                                "enum": ["json", "csv"],
                            },
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Data response",
                            "content": {
                                "application/json": {"schema": {"type": "object"}},
                            },
                        }
                    },
                }
            }
        },
    }
    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/data", methods=["GET"])
    def data_endpoint():
        format_param = request.args.get("format", "json")

        if format_param == "csv":
            return "name,age\nJohn,25", 200, {"Content-Type": ""}
        return jsonify({"name": "John", "age": 25})

    port = app_runner.run_flask_app(app)

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "-c response_schema_conformance",
            "--mode=positive",
            "--phases=fuzzing",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


@pytest.fixture
def schema(ctx):
    return ctx.openapi.build_schema(
        {
            "/test": {
                "get": {
                    "responses": {
                        "200": {"description": "Successful response"},
                        "400": {"description": "Bad request"},
                    }
                }
            }
        }
    )


@pytest.mark.parametrize(
    "expected_statuses",
    [
        None,  # Default case
        ["404"],
        ["405"],
        ["2xx", "404"],
        ["200"],
        ["200", "404"],
        ["2xx"],
        ["4xx"],
        # Invalid status code
        ["200", "600"],
        # Invalid wildcard
        ["xxx"],
        ["200", 201, 400, 401],
    ],
)
def test_positive_data_acceptance(ctx, cli, snapshot_cli, schema, openapi3_base_url, expected_statuses):
    schema_path = ctx.makefile(schema)
    kwargs = {}
    if expected_statuses is not None:
        kwargs["config"] = {"checks": {"positive_data_acceptance": {"expected-statuses": expected_statuses}}}

    assert (
        cli.run(
            str(schema_path),
            f"--url={openapi3_base_url}",
            "--max-examples=5",
            "--checks=positive_data_acceptance",
            **kwargs,
        )
        == snapshot_cli
    )


def test_not_a_server_error(cli, snapshot_cli, openapi3_schema_url):
    assert (
        cli.run(
            openapi3_schema_url,
            "--max-examples=5",
            "--checks=not_a_server_error",
            "--mode=positive",
            config={"checks": {"not_a_server_error": {"expected-statuses": ["2xx", "4xx", "500"]}}},
        )
        == snapshot_cli
    )
