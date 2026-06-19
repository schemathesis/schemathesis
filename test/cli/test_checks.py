import uuid
import xml.etree.ElementTree as ET

import pytest
from _pytest.main import ExitCode
from flask import jsonify, request

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


def test_negative_data_rejection(ctx, cli):
    api = ctx.openapi.apps.success()
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
    cli.run_and_assert(
        str(schema_path),
        f"--url={api.base_url}/api",
        "--checks",
        "negative_data_rejection",
        "--mode",
        "negative",
        "--max-examples=5",
        exit_code=ExitCode.TESTS_FAILED,
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_negative_data_rejection_displays_all_cases(ctx, cli, snapshot_cli):
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
    app = ctx.openapi.make_flask_app_from_schema(raw_schema)

    @app.route("/test", methods=["GET"])
    def test_endpoint():
        header = request.headers.get("Accept-Language")
        if header not in ["en-US", "fr-FR"]:
            return jsonify({"message": "negative"}), 406
        return jsonify({"incorrect": "positive"}), 200

    assert (
        cli.run_openapi_app(
            app,
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


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_negative_data_rejection_path_parameter_type_mutation(ctx, cli, snapshot_cli):
    # String value for an integer path parameter serializes to the same URL as the integer.
    # E.g., string "7" becomes /api/run/7 - indistinguishable from integer 7.
    app, raw_schema = ctx.openapi.make_flask_app(
        {
            "/api/run/{id}": {
                "post": {
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "Success"}},
                }
            }
        }
    )

    @app.route("/api/run/<path:id>", methods=["POST"])
    def run_endpoint(id):
        # Server accepts numeric-looking paths (including negative numbers like -1, -42)
        try:
            int(id)
            return "", 200
        except ValueError:
            return "", 400

    assert (
        cli.run_openapi_app(
            app,
            "--checks=negative_data_rejection",
            "--mode=negative",
            "--phases=fuzzing",
            "--max-examples=200",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_negative_data_rejection_path_parameter_number_type_mutation(ctx, cli, snapshot_cli):
    # Like the integer variant, string mutations for a float path parameter can decode to valid floats.
    # E.g., "+1.5" becomes /api/rate/%2B1.5 - URL-decoded to "+1.5" which float() accepts.
    app, raw_schema = ctx.openapi.make_flask_app(
        {
            "/api/rate/{value}": {
                "get": {
                    "parameters": [{"name": "value", "in": "path", "required": True, "schema": {"type": "number"}}],
                    "responses": {"200": {"description": "Success"}},
                }
            }
        }
    )

    @app.route("/api/rate/<path:value>", methods=["GET"])
    def rate(value):
        try:
            float(value)
            return "", 200
        except ValueError:
            return "", 400

    assert (
        cli.run_openapi_app(
            app,
            "--checks=negative_data_rejection",
            "--mode=negative",
            "--phases=fuzzing",
            "--max-examples=200",
        )
        == snapshot_cli
    )


def test_negative_data_rejection_uuid_path_param_with_pattern_no_false_positive(ctx, cli, app_runner):
    # See GH-3603
    # UUID path param with an explicit lowercase pattern — when the fuzzing phase injects
    # a captured valid UUID, the positive value must NOT trigger a failure
    app, raw_schema = ctx.openapi.make_flask_app(
        {
            "/api/tasks": {
                "post": {
                    "operationId": "createTask",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["title"],
                                    "properties": {"title": {"type": "string", "minLength": 1}},
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["id", "title"],
                                        "properties": {
                                            "id": {"type": "string", "format": "uuid"},
                                            "title": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/tasks/{taskId}": {
                "get": {
                    "operationId": "getTask",
                    "parameters": [
                        {
                            "name": "taskId",
                            "in": "path",
                            "required": True,
                            "schema": {
                                "type": "string",
                                "format": "uuid",
                                "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                            },
                        }
                    ],
                    "responses": {
                        "200": {"description": "OK"},
                        "404": {"description": "Not found"},
                    },
                }
            },
        }
    )
    tasks = {}

    @app.route("/api/tasks", methods=["POST"])
    def create_task():
        body = request.json
        if not isinstance(body, dict):
            return jsonify({"error": "body must be an object"}), 400
        title = body.get("title")
        if not isinstance(title, str) or not title:
            return jsonify({"error": "title must be a non-empty string"}), 400
        task_id = str(uuid.uuid4())
        task = {"id": task_id, "title": title}
        tasks[task_id] = task
        return jsonify(task), 201

    @app.route("/api/tasks/<task_id>", methods=["GET"])
    def get_task(task_id):
        return jsonify(tasks[task_id]) if task_id in tasks else ("", 404)

    cli.run_and_assert(
        app_runner.openapi_url(app),
        "--checks=negative_data_rejection",
        "--mode=negative",
        "--phases=stateful,fuzzing",
        "--max-examples=200",
        exit_code=ExitCode.OK,
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_negative_data_rejection_xml_body_string_type_no_false_positive(ctx, cli, snapshot_cli):
    # Type mutations for XML body string fields serialize all non-string values to their string
    # representations via _escape_xml (False -> "False", 0 -> "0", None -> "").
    # These are indistinguishable from valid strings at the wire level, so no false positive
    # should be reported when the API correctly accepts the request.
    # See GH-3525
    app, raw_schema = ctx.openapi.make_flask_app(
        {
            "/api/negotiations/negotiation": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/xml": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "payment_amount": {"type": "string"},
                                        "payment_method_id": {"type": "string"},
                                    },
                                    "required": ["payment_amount", "payment_method_id"],
                                }
                            }
                        },
                    },
                    "responses": {"201": {"description": "Created"}},
                }
            }
        }
    )

    @app.route("/api/negotiations/negotiation", methods=["POST"])
    def negotiation():
        # Accept only valid XML objects with both required string fields present.
        # Note: XML strips type info - boolean False becomes "False", integer 0 becomes "0",
        # so we validate structure (required fields present) not value types.
        try:
            root = ET.fromstring(request.data)
            if root.find("payment_amount") is None or root.find("payment_method_id") is None:
                return "", 400
            return "", 201
        except ET.ParseError:
            return "", 400

    assert (
        cli.run_openapi_app(
            app,
            "--checks=negative_data_rejection",
            "--mode=negative",
            "--phases=fuzzing",
            "--max-examples=200",
        )
        == snapshot_cli
    )


def test_negative_data_rejection_number_body_field_accepts_integer_no_false_positive(ctx, cli, app_runner):
    # See GH-3697
    # `type: number` in JSON Schema accepts integers — any integer is a valid number.
    # Sending an integer (e.g., score=3) for a `type: number` field must NOT trigger
    # AcceptedNegativeData because the server is correct to accept it.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["score"],
                                    "properties": {
                                        "score": {"type": "number"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/data", methods=["POST"])
    def data():
        body = request.get_json(silent=True, force=True)
        if not isinstance(body, dict):
            return jsonify({"error": "invalid body"}), 422
        score = body.get("score")
        # Accept integers and floats; reject booleans (bool is a subclass of int in Python)
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            return jsonify({"error": "score must be a number"}), 422
        return jsonify({"ok": True}), 200

    cli.run_and_assert(
        app_runner.openapi_url(app),
        "--checks=negative_data_rejection",
        "--mode=negative",
        "--phases=fuzzing",
        "--max-examples=200",
        exit_code=ExitCode.OK,
    )


def test_negative_data_rejection_query_integer_param_accepts_numeric_string_no_false_positive(ctx, cli, app_runner):
    # See GH-3712
    app, _ = ctx.openapi.make_flask_app(
        {
            "/users": {
                "get": {
                    "parameters": [
                        {
                            "name": "page_size",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/users")
    def users():
        values = request.args.getlist("page_size")
        if len(values) > 1:
            # Object/array mutations serialize to multiple repeated query params; reject them.
            return jsonify({"error": "too many page_size values"}), 422
        if values:
            try:
                int(values[0])
            except (ValueError, TypeError):
                return jsonify({"error": "invalid page_size"}), 422
        return jsonify({"results": []}), 200

    cli.run_and_assert(
        app_runner.openapi_url(app),
        "--checks=negative_data_rejection",
        "--mode=negative",
        "--phases=fuzzing",
        exit_code=ExitCode.OK,
    )


def test_negative_data_rejection_boolean_query_param_with_api_key_query_auth_no_false_positive(ctx, cli, app_runner):
    # See GH-4254. An API key in the query must not flip a valid parameter to a schema violation.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/check": {
                "get": {
                    "parameters": [{"name": "flag", "in": "query", "required": False, "schema": {"type": "boolean"}}],
                    "responses": {
                        "200": {"description": "OK"},
                        "401": {"description": "Unauthorized"},
                        "422": {"description": "Bad Request"},
                    },
                }
            }
        },
        security=[{"apikey": []}],
        components={"securitySchemes": {"apikey": {"type": "apiKey", "name": "apikey", "in": "query"}}},
    )

    @app.route("/check")
    def check():
        if request.args.get("apikey") != "valid-key":
            return jsonify({"error": "unauthorized"}), 401
        values = request.args.getlist("flag")
        if len(values) > 1:
            return jsonify({"error": "too many flag values"}), 422
        if values and values[0].lower() not in {"true", "false", "1", "0"}:
            return jsonify({"error": "invalid flag"}), 422
        return jsonify({"ok": True}), 200

    cli.run_and_assert(
        app_runner.openapi_url(app),
        "--phases=coverage",
        exit_code=ExitCode.OK,
        config={
            "generation": {"allow-extra-parameters": False, "unique-inputs": True},
            "phases": {"coverage": {"unexpected-methods": []}},
            "auth": {"openapi": {"apikey": {"api_key": "valid-key"}}},
        },
    )


def test_negative_data_rejection_array_of_strings_boolean_collision(ctx, cli, snapshot_cli):
    # See GH-2913
    app, raw_schema = ctx.openapi.make_flask_app(
        {
            "/api/example/v1/page": {
                "get": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "names",
                            "schema": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "example": ["TEST"],
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"data": {"type": "array"}},
                                    }
                                }
                            },
                        },
                        "400": {
                            "description": "Bad Request",
                        },
                    },
                }
            }
        }
    )

    @app.route("/api/example/v1/page", methods=["GET"])
    def get_page():
        names_value = request.args.get("names")
        if names_value is None:
            # Parameter not provided at all
            names = []
        elif names_value == "":
            # Empty value (?names=)
            names = []
        else:
            # Comma-separated values for array (style: form, explode: false)
            names = names_value.split(",")

        return jsonify({"data": names}), 200

    assert (
        cli.run_openapi_app(
            app,
            "--checks=negative_data_rejection",
            "--mode=negative",
            "--phases=fuzzing",
            "--max-examples=50",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    ["version", "kwargs"],
    [
        pytest.param(
            "2.0",
            {"securityDefinitions": {"basic_auth": {"type": "basic"}}},
            id="openapi2",
        ),
        pytest.param(
            "3.0.2",
            {"components": {"securitySchemes": {"basic_auth": {"type": "http", "scheme": "basic"}}}},
            id="openapi3",
        ),
    ],
)
def test_optional_auth_should_not_trigger_ignored_auth_check(ctx, cli, snapshot_cli, version, kwargs):
    # See GH-3052
    app, raw_schema = ctx.openapi.make_flask_app(
        {
            "/": {
                "get": {
                    "security": [
                        {},
                        {"basic_auth": []},
                    ],
                    "responses": {"200": {"description": "200 OK"}},
                }
            }
        },
        version=version,
        **kwargs,
    )

    @app.route("/", methods=["GET"])
    def data_endpoint():
        return jsonify({"status": "Ok"})

    assert cli.run_openapi_app(app, "-c ignored_auth", "--phases=fuzzing", "--max-examples=3") == snapshot_cli


@pytest.mark.parametrize(
    ["version", "kwargs"],
    [
        pytest.param(
            "2.0",
            {"securityDefinitions": {"basic_auth": {"type": "basic"}}},
            id="openapi2",
        ),
        pytest.param(
            "3.0.2",
            {"components": {"securitySchemes": {"basic_auth": {"type": "http", "scheme": "basic"}}}},
            id="openapi3",
        ),
    ],
)
def test_optional_auth_should_not_trigger_missing_required_header(ctx, cli, snapshot_cli, version, kwargs):
    app, raw_schema = ctx.openapi.make_flask_app(
        {
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
        version=version,
        **kwargs,
    )

    @app.route("/", methods=["GET"])
    def data_endpoint():
        return jsonify({"status": "Ok"})

    assert cli.run_openapi_app(app, "-c missing_required_header") == snapshot_cli


def test_format_parameter_csv_response(ctx, cli, snapshot_cli):
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
    app = ctx.openapi.make_flask_app_from_schema(raw_schema)

    @app.route("/data", methods=["GET"])
    def data_endpoint():
        format_param = request.args.get("format", "json")

        if format_param == "csv":
            return "name,age\nJohn,25", 200, {"Content-Type": ""}
        return jsonify({"name": "John", "age": 25})

    assert (
        cli.run_openapi_app(
            app,
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
def test_positive_data_acceptance(ctx, cli, snapshot_cli, schema, expected_statuses):
    api = ctx.openapi.apps.success()
    schema_path = ctx.makefile(schema)
    kwargs = {}
    if expected_statuses is not None:
        kwargs["config"] = {"checks": {"positive_data_acceptance": {"expected-statuses": expected_statuses}}}

    assert (
        cli.run(
            str(schema_path),
            f"--url={api.base_url}/api",
            "--max-examples=5",
            "--checks=positive_data_acceptance",
            **kwargs,
        )
        == snapshot_cli
    )


def test_not_a_server_error(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success_and_failure()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=5",
            "--checks=not_a_server_error",
            "--mode=positive",
            config={"checks": {"not_a_server_error": {"expected-statuses": ["2xx", "4xx", "500"]}}},
        )
        == snapshot_cli
    )


@pytest.fixture
def app(ctx):
    _schema = {
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
    app = ctx.openapi.make_flask_app_from_schema(_schema)

    organizations = {}
    next_id = 1

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


def test_response_schema_conformance(ctx, cli, app_runner, snapshot_cli, app):
    @app.route("/organizations/", methods=["GET"])
    def list_organizations():
        return [], 200

    @app.route("/organizations/<slug>/", methods=["GET"])
    def get_organization(slug):
        return jsonify({"name": slug}), 200

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
    base_url = app_runner.openapi_url(app, path="")
    # There should be no empty `organization_slug` generated which will lead to request being handled by `GET /organizations/`
    # onstead of `GET /organizations/{organization_slug}/` and will give a response schema conformance error
    assert (
        cli.run(
            str(schema_file),
            f"--url={base_url}",
            "-c response_schema_conformance",
            "--max-examples=10",
        )
        == snapshot_cli
    )


def test_ensure_resource_availability_does_not_trigger_on_subsequent_error(cli, snapshot_cli, app):
    assert (
        cli.run_openapi_app(
            app,
            "-c ensure_resource_availability",
            "--max-examples=50",
            "--phases=stateful",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_use_after_free_does_not_trigger_on_error(cli, snapshot_cli, app):
    assert (
        cli.run_openapi_app(
            app,
            "-c use_after_free",
            "--max-examples=50",
            "--phases=stateful",
        )
        == snapshot_cli
    )


def test_negative_data_rejection_array_min_items_zero_no_false_positive(ctx, cli, snapshot_cli):
    # See GH-3056
    raw_schema = {
        "openapi": "3.1.1",
        "info": {"title": "Test API", "version": "0.1.0"},
        "paths": {
            "/no-param": {
                "get": {
                    "operationId": "noParam",
                    "responses": {
                        "200": {
                            "description": "Simple example",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "foo": {
                                                "type": "string",
                                                "example": "bar",
                                            }
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/with-param": {
                "get": {
                    "operationId": "withParam",
                    "parameters": [
                        {
                            "name": "ids",
                            "in": "query",
                            "required": True,
                            "schema": {
                                "type": "array",
                                "items": {"type": "string"},
                                # Explicitly allows empty array
                                "minItems": 0,
                            },
                            "style": "form",
                            # Comma-separated format
                            "explode": False,
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Simple example",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"foo": {"type": "string", "example": "bar"}},
                                    }
                                }
                            },
                        }
                    },
                }
            },
        },
    }

    app = ctx.openapi.make_flask_app_from_schema(raw_schema)

    @app.route("/no-param", methods=["GET"])
    def no_param():
        return jsonify({"foo": "bar"}), 200

    @app.route("/with-param", methods=["GET"])
    def with_param():
        # Check if 'ids' parameter is present in the URL
        if "ids" not in request.args:
            # Required parameter is missing - this is truly invalid
            return jsonify({"error": "Missing required parameter: ids"}), 400
        return jsonify({"foo": "bar"}), 200

    assert (
        cli.run_openapi_app(
            app,
            "--checks=negative_data_rejection",
            "--mode=negative",
            "--phases=fuzzing",
            "--suppress-health-check=all",
            "--max-examples=20",
        )
        == snapshot_cli
    )


def test_negative_data_rejection_form_data_empty_string_false_positive(ctx, cli, snapshot_cli):
    # Empty string in form data should not be treated as None/null for required string fields
    app, raw_schema = ctx.openapi.make_flask_app(
        {
            "/suggest": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/x-www-form-urlencoded": {
                                "schema": {
                                    "type": "object",
                                    "required": ["text"],
                                    "properties": {
                                        "text": {
                                            "type": "string",
                                            "description": "input text",
                                        },
                                    },
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {
                        "200": {"description": "OK"},
                        "400": {"description": "Bad Request"},
                    },
                }
            }
        }
    )

    @app.route("/suggest", methods=["POST"])
    def suggest():
        text = request.form.get("text")
        # Empty string is valid for a required string field
        if text is None:
            return jsonify({"error": "Missing required field"}), 400
        return jsonify({"results": []}), 200

    assert (
        cli.run_openapi_app(
            app,
            "--checks=negative_data_rejection",
            "--mode=negative",
            "--max-examples=20",
        )
        == snapshot_cli
    )


def test_negative_data_rejection_fuzzing_phase_metadata(ctx, cli, app_runner):
    # Use a simple schema with single property to avoid multiple mutation conflicts
    app, raw_schema = ctx.openapi.make_flask_app(
        {
            "/users": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {"400": {"description": "Bad Request"}},
                }
            }
        }
    )

    @app.route("/users", methods=["POST"])
    def create_user():
        return jsonify({"result": "ok"}), 200

    result = cli.run_and_assert(
        app_runner.openapi_url(app),
        "--checks=negative_data_rejection",
        "--mode=negative",
        "--phases=fuzzing",
        "--max-examples=25",
        "--continue-on-failure",
        exit_code=ExitCode.TESTS_FAILED,
    )
    assert "API accepted schema-violating request" in result.stdout
    assert "Invalid component: in body" in result.stdout


@pytest.mark.parametrize(
    "body_schema",
    [
        pytest.param(
            {"type": "object", "properties": {"my_param": {"type": "number"}}},
            id="optional_properties",
        ),
        pytest.param(
            {"type": "object"},
            id="no_properties",
        ),
    ],
)
def test_positive_data_acceptance_required_form_body_no_false_positive(ctx, cli, snapshot_cli, body_schema):
    # When requestBody.required=true but inner schema allows empty object,
    # coverage generates {} which serializes to no body content for form-urlencoded.
    # This should NOT trigger a false positive "API rejected schema-compliant request".
    app, raw_schema = ctx.openapi.make_flask_app(
        {
            "/my-method": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/x-www-form-urlencoded": {"schema": body_schema}},
                    },
                    "responses": {
                        "200": {"description": "OK"},
                        "400": {"description": "Bad Request"},
                    },
                }
            }
        }
    )

    @app.route("/my-method", methods=["POST"])
    def my_method():
        if not request.data and not request.form:
            return jsonify({"error": "Request body is required"}), 400
        return jsonify({"result": "ok"}), 200

    assert (
        cli.run_openapi_app(
            app,
            "--checks=positive_data_acceptance",
            "--phases=coverage",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_positive_data_acceptance_additional_properties_hint(ctx, cli, snapshot_cli):
    # When Hypothesis adds extra properties to a schema without `additionalProperties: false`,
    # the failure message should include a hint explaining the likely cause.
    app, raw_schema = ctx.openapi.make_flask_app(
        {
            "/session": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "zones": {
                                            "type": "array",
                                            "minItems": 1,
                                            "maxItems": 4,
                                            "items": {"type": "integer", "minimum": 0, "maximum": 4},
                                            "uniqueItems": True,
                                        }
                                    },
                                    "required": ["zones"],
                                    # No `additionalProperties: false` — Hypothesis will add extra keys
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "OK"},
                        "422": {"description": "Unprocessable Entity"},
                    },
                }
            }
        }
    )

    @app.route("/session", methods=["POST"])
    def session():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Expected object"}), 422
        # Reject if any key beyond `zones` is present
        if set(data.keys()) - {"zones"}:
            return jsonify({"error": "Unexpected fields"}), 422
        return jsonify({"ok": True}), 200

    assert (
        cli.run_openapi_app(
            app,
            "--checks=positive_data_acceptance",
            "--phases=fuzzing",
            "--max-examples=50",
            "--seed=1",
        )
        == snapshot_cli
    )


def test_positive_data_acceptance_body_list_examples_verbatim(ctx, cli, app_runner):
    app, raw_schema = ctx.openapi.make_flask_app(
        {
            "/api/payments": {
                "post": {
                    "operationId": "getPayments",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/PaymentsRequest"},
                                # OAS3 Examples Object should be a dict, but some schemas use a list here.
                                # Each element is an OAS3-style Example Object with a "value" key.
                                "examples": [
                                    {
                                        "value": {
                                            "request": {
                                                "payment": {"SupplierAccount": "5411707635"},
                                                "audit": {"requestedSystem": "a22c6ad7"},
                                            }
                                        }
                                    }
                                ],
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "OK"},
                        "400": {"description": "Bad Request"},
                    },
                }
            }
        },
        version="3.1.0",
        components={
            "schemas": {
                "PaymentsRequest": {
                    "type": "object",
                    "properties": {
                        "request": {
                            "type": "object",
                            "properties": {
                                "payment": {"type": "object"},
                                "audit": {"type": "object"},
                            },
                            "required": ["payment", "audit"],
                        }
                    },
                    "required": ["request"],
                }
            }
        },
    )

    @app.route("/api/payments", methods=["POST"])
    def payments_list_examples():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or "request" not in body:
            return (
                jsonify(
                    {
                        "Message": "Parameter 'request' is not found within the request content body.",
                        "ExceptionType": "Boom",
                    }
                ),
                400,
            )
        return jsonify({"result": "ok"}), 200

    schema_url = app_runner.openapi_url(app)

    cli.run_and_assert(
        schema_url,
        "--checks=positive_data_acceptance",
        "--phases=examples",
        exit_code=ExitCode.OK,
    )
    cli.run_and_assert(
        schema_url,
        "--checks=positive_data_acceptance",
        "--phases=fuzzing",
        "--max-examples=50",
        exit_code=ExitCode.OK,
    )


def test_missing_required_header_body_first_server_no_false_negative(ctx, cli, app_runner):
    # Server validates body before header. With a valid template body the missing-header case
    # reaches header validation (400), so the check correctly passes — no false negative.
    app = ctx.openapi.make_flask_app_from_schema(
        {
            "openapi": "3.0.0",
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "paths": {
                "/items/{kind}": {
                    "post": {
                        "parameters": [
                            {
                                "name": "kind",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string", "enum": ["Foo", "Bar"]},
                            },
                            {
                                "name": "X-Required-Header",
                                "in": "header",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                        ],
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "oneOf": [
                                            {"type": "null"},
                                            {
                                                "type": "object",
                                                "properties": {"value": {"type": "string"}},
                                                "required": ["value"],
                                            },
                                        ]
                                    }
                                }
                            }
                        },
                        "responses": {
                            "200": {"description": "OK"},
                            "400": {"description": "Missing or invalid header"},
                            "422": {"description": "Invalid JSON body"},
                        },
                    }
                }
            },
        }
    )

    @app.route("/items/<kind>", methods=["POST"])
    def items(kind):
        body = request.get_json(silent=True, force=True)
        # Server validates body before header (common in framework middleware)
        if body is None:
            return jsonify({"error": "body must not be null"}), 422
        header = request.headers.get("X-Required-Header")
        if not header:
            return jsonify({"error": "missing required header"}), 400
        return jsonify({"ok": True}), 200

    cli.run_and_assert(
        app_runner.openapi_url(app),
        "--checks=missing_required_header",
        "--phases=coverage",
        exit_code=ExitCode.OK,
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_response_schema_conformance_reports_all_errors_in_cli(ctx, cli, snapshot_cli):
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
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "name": {"type": "string"},
                                        },
                                        "required": ["id", "name"],
                                    }
                                }
                            },
                        }
                    }
                }
            }
        }
    )

    @app.route("/data")
    def data():
        # Always returns a response that violates schema in two ways:
        # 'id' is a string (should be integer), 'name' is an integer (should be string)
        return jsonify({"id": "not-an-int", "name": 42})

    assert (
        cli.run_openapi_app(
            app,
            "--checks=response_schema_conformance",
            "--max-examples=1",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_response_schema_conformance_groups_multiple_violations(ctx, cli, snapshot_cli):
    # 4 violations on one response should render under a single header with a violation count,
    # not 4 separate `- Response violates schema` blocks.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/user": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "email": {"type": "string", "format": "email"},
                                            "age": {"type": "integer", "minimum": 0},
                                            "status": {"type": "string", "enum": ["active", "inactive"]},
                                            "name": {"type": "string", "minLength": 1},
                                        },
                                        "required": ["email", "age", "status", "name"],
                                    }
                                }
                            },
                        }
                    }
                }
            }
        }
    )

    @app.route("/api/user")
    def user():
        return jsonify({"email": "abc", "age": -1, "status": "pending", "name": ""})

    assert (
        cli.run_openapi_app(
            app,
            "--checks=response_schema_conformance",
            "--max-examples=1",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_negative_data_rejection_nested_body_description(ctx, cli, snapshot_cli):
    # Schema with no type at root/intermediate levels so coverage only generates
    # property constraint violations, surfacing the nested path in the description
    app, _ = ctx.openapi.make_flask_app(
        {
            "/payment": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "properties": {
                                        "request": {
                                            "properties": {
                                                "payment": {
                                                    "properties": {
                                                        "SupplierAccount": {
                                                            "minimum": 10,
                                                        }
                                                    },
                                                }
                                            },
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/payment", methods=["POST"])
    def payment():
        return jsonify({}), 200

    assert (
        cli.run_openapi_app(
            app,
            "--checks=negative_data_rejection",
            "--phases=coverage",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_readonly_in_allof_branch_does_not_collapse_positive_generation(ctx, cli, snapshot_cli):
    # Server bug behind valid POST body; reachable only when positive-mode generates valid data.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/widgets": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Widget"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "schemas": {
                "WidgetFields": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 30}},
                    "required": ["name"],
                },
                "Widget": {
                    "allOf": [
                        {"$ref": "#/components/schemas/WidgetFields"},
                        {"type": "object", "properties": {"id": {"type": "integer", "readOnly": True}}},
                    ],
                    "required": ["id", "name"],
                },
            }
        },
    )

    @app.route("/widgets", methods=["POST"])
    def create_widget():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not data["name"]:
            return "", 400
        raise RuntimeError("planted bug")

    assert (
        cli.run_openapi_app(
            app,
            "--mode=positive",
            "--phases=fuzzing",
            "--max-examples=10",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_response_schema_conformance_large_pattern(ctx, cli, snapshot_cli):
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
                                        "type": "string",
                                        "pattern": r"^.{0,404600}$",
                                    }
                                }
                            },
                        }
                    }
                }
            }
        }
    )

    @app.route("/data")
    def data():
        return jsonify("")

    assert (
        cli.run_openapi_app(
            app,
            "--checks=response_schema_conformance",
            "--max-examples=1",
        )
        == snapshot_cli
    )
