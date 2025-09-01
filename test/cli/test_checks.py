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


def test_optional_auth_should_not_trigger_ignored_auth_check(app_runner, cli, snapshot_cli):
    # See GH-3052
    raw_schema = {
        "openapi": "3.0.3",
        "info": {"title": "example", "version": "1.0.0"},
        "paths": {
            "/": {
                "get": {
                    "security": [
                        {},
                        {"basic_auth": []},
                    ],
                    "responses": {"200": {"description": "200 OK"}},
                }
            },
        },
        "components": {
            "securitySchemes": {"basic_auth": {"type": "http", "scheme": "basic"}},
        },
    }
    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/", methods=["GET"])
    def data_endpoint():
        return jsonify({"status": "Ok"})

    port = app_runner.run_flask_app(app)

    assert (
        cli.run(f"http://127.0.0.1:{port}/openapi.json", "-c ignored_auth", "--phases=fuzzing", "--max-examples=3")
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


@pytest.fixture
def app():
    app = Flask(__name__)

    organizations = {}
    next_id = 1

    @app.route("/openapi.json")
    def openapi():
        return {
            "openapi": "3.1.0",
            "info": {"title": "Test API", "version": "0.1.0"},
            "paths": {
                "/organizations/": {
                    "post": {
                        "operationId": "organizations:create",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"name": {"type": "string"}},
                                        "required": ["name"],
                                    }
                                }
                            },
                            "required": True,
                        },
                        "responses": {
                            "201": {
                                "description": "Created",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                                        }
                                    }
                                },
                                "links": {
                                    "delete": {
                                        "operationId": "organizations:delete",
                                        "parameters": {
                                            "organization_id": "$response.body#/id",
                                        },
                                    },
                                    "create_project": {
                                        "operationId": "organizations:projects:create",
                                        "parameters": {
                                            "organization_id": "$response.body#/id",
                                        },
                                    },
                                },
                            }
                        },
                    }
                },
                "/organizations/{organization_id}/": {
                    "delete": {
                        "operationId": "organizations:delete",
                        "parameters": [
                            {
                                "name": "organization_id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            }
                        ],
                        "responses": {
                            "204": {"description": "No Content"},
                            "404": {"description": "Not Found"},
                        },
                    }
                },
                "/organizations/{organization_id}/projects/": {
                    "post": {
                        "operationId": "organizations:projects:create",
                        "parameters": [
                            {"name": "organization_id", "in": "path", "required": True, "schema": {"type": "integer"}}
                        ],
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"name": {"type": "string"}},
                                        "required": ["name"],
                                    }
                                }
                            },
                            "required": True,
                        },
                        "responses": {
                            "201": {"description": "Created"},
                            "404": {"description": "Not Found"},
                            "422": {"description": "Unprocessable Content"},
                        },
                    }
                },
            },
        }

    @app.route("/organizations/", methods=["POST"])
    def create_organization():
        nonlocal next_id
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({"error": "Must be object"}), 422
        if "name" not in data:
            return jsonify({"error": "Name is missing"}), 422

        organizations[next_id] = data
        response = jsonify({"id": next_id, "name": data["name"]})
        next_id += 1
        return response, 201

    @app.route("/organizations/<int:organization_id>/", methods=["DELETE"])
    def delete_organization(organization_id):
        if organization_id not in organizations:
            return jsonify({"error": "Not found"}), 404
        del organizations[organization_id]
        return "", 204

    @app.route("/organizations/<int:organization_id>/projects/", methods=["POST"])
    def create_project(organization_id):
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({"error": "Must be object"}), 422
        if "name" not in data:
            return jsonify({"error": "Name is missing"}), 422

        # Only check organization existence AFTER validation
        if organization_id not in organizations:
            return jsonify({"error": "Not found"}), 404

        return jsonify({"id": 1}), 201

    return app


def test_response_schema_conformance(ctx, app_runner, cli, snapshot_cli, app):
    @app.route("/organizations/", methods=["GET"])
    def list_organizations():
        return [], 200

    schema_file = ctx.openapi.write_schema(
        {
            "/organizations/": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "array"},
                                }
                            }
                        }
                    }
                }
            },
            "/organizations/{organization_slug}/": {
                "get": {
                    "parameters": [
                        {
                            "name": "organization_slug",
                            "in": "path",
                            "schema": {"type": "string", "minLength": 1},
                        }
                    ],
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/Organization",
                                    }
                                }
                            }
                        }
                    },
                }
            },
        },
        components={
            "schemas": {
                "Organization": {
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                    },
                    "type": "object",
                }
            }
        },
    )
    port = app_runner.run_flask_app(app)
    # There should be no empty `organization_slug` generated which will lead to request being handled by `GET /organizations/`
    # onstead of `GET /organizations/{organization_slug}/` and will give a response schema conformance error
    assert (
        cli.run(
            str(schema_file),
            f"--url=http://127.0.0.1:{port}",
            "-c response_schema_conformance",
            "--max-examples=10",
        )
        == snapshot_cli
    )


def test_ensure_resource_availability_does_not_trigger_on_subsequent_error(app_runner, cli, snapshot_cli, app):
    port = app_runner.run_flask_app(app)

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "-c ensure_resource_availability",
            "--max-examples=50",
            "--phases=stateful",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_use_after_free_does_not_trigger_on_error(app_runner, cli, snapshot_cli, app):
    port = app_runner.run_flask_app(app)

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "-c use_after_free",
            "--max-examples=50",
            "--phases=stateful",
        )
        == snapshot_cli
    )
