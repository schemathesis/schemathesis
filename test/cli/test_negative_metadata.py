import json
import re
import uuid

import pytest
from _pytest.main import ExitCode
from flask import jsonify, request

import schemathesis
from schemathesis.engine import Status, events

UUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_format_password_false_positive(ctx, cli, snapshot_cli):
    # GH-3480: Schemathesis incorrectly reports data as invalid for format: password
    # In OpenAPI 3.0, `format` is an annotation and does NOT impose validation constraints by itself.
    # With only `type: string, format: password`, any string is valid.
    # Schemathesis incorrectly treats generated data as schema-violating and expects rejection.
    #
    # The coverage phase generates format-specific negative cases via _negative_format,
    # which incorrectly claims values don't match the 'password' format.
    app, _ = ctx.openapi.make_flask_app(
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

    # No failure should be reported since any string is valid for format: password in OpenAPI 3.0
    assert (
        cli.run_openapi_app(
            app,
            "--checks=negative_data_rejection",
            "--phases=coverage",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_text_plain_negative_becomes_valid_after_serialization(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
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

    @app.route("/some-string-endpoint", methods=["PUT"])
    def string_endpoint():
        return "", 200

    assert (
        cli.run_openapi_app(
            app,
            "--checks=negative_data_rejection",
            "--mode=negative",
            "--phases=fuzzing",
            "--max-examples=5",
            "--seed=1",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True, replace_invalid_component=True)
def test_text_plain_with_query_negative_still_fails(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
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

    @app.route("/endpoint", methods=["PUT"])
    def endpoint():
        return "", 200

    assert (
        cli.run_openapi_app(
            app,
            "--checks=negative_data_rejection",
            "--mode=negative",
            "--phases=fuzzing",
            "--max-examples=10",
            "--seed=3",
        )
        == snapshot_cli
    )


# High example count: a negated body with only valid declared query values is a rare draw.
def test_text_plain_with_valid_boolean_query_not_reported(ctx, cli, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/endpoint": {
                "post": {
                    "parameters": [{"in": "query", "name": "q", "required": False, "schema": {"type": "boolean"}}],
                    "requestBody": {
                        "required": True,
                        "content": {"text/plain": {"schema": {"type": "string"}}},
                    },
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Bad"}},
                }
            }
        }
    )

    @app.route("/endpoint", methods=["POST"])
    def endpoint():
        values = request.args.getlist("q")
        if len(values) > 1 or (values and values[0] not in ("true", "false")):
            return jsonify({}), 400
        return jsonify({}), 200

    cli.run_and_assert(
        app_runner.openapi_url(app),
        "--checks=negative_data_rejection",
        "--phases=fuzzing",
        "--max-examples=100",
        "--seed=1",
        exit_code=ExitCode.OK,
    )


def test_removed_auth_parameter_not_reapplied_no_credentials(ctx):
    # No credentials configured — server always returns 401; negative_data_rejection must not fire.
    api = ctx.openapi.apps.basic()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.seed = 42
    schema.config.generation.update(modes=[schemathesis.GenerationMode.NEGATIVE])
    schema.config.phases.examples.enabled = False
    schema.config.phases.coverage.enabled = False
    schema.config.phases.stateful.enabled = False
    schema.config.phases.fuzzing.generation.update(max_examples=20)
    schema.config.checks.update(included_check_names=["negative_data_rejection"])

    failures = []
    for event in schemathesis.engine.from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished) and event.status == Status.FAILURE:
            failures.append(event)

    assert not failures, f"unexpected negative_data_rejection failures: {failures}"


def test_removed_auth_parameter_not_reapplied_with_credentials(ctx):
    # When auth credentials are configured, a removal mutation must leave the
    # Authorization header absent — interceptor must not re-add it — so the
    # server returns 401 and negative_data_rejection does not fire.
    api = ctx.openapi.apps.basic()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.seed = 42
    schema.config.generation.update(modes=[schemathesis.GenerationMode.NEGATIVE])
    schema.config.phases.examples.enabled = False
    schema.config.phases.coverage.enabled = False
    schema.config.phases.stateful.enabled = False
    schema.config.phases.fuzzing.generation.update(max_examples=50)
    schema.config.auth.update(basic=("test", "test"))
    schema.config.checks.update(included_check_names=["negative_data_rejection"])

    failures = []
    for event in schemathesis.engine.from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished) and event.status == Status.FAILURE:
            failures.append(event)

    auth_removed = [r for r in api.requests if "Authorization" not in r.headers]
    assert auth_removed, "no auth-removal mutation fired in 50 examples"
    assert not failures, f"unexpected negative_data_rejection failures: {failures}"


def test_hook_modified_path_parameter_no_false_positive(ctx, cli):
    # GH-4277: a before_call hook clamps an out-of-range path param to a valid value;
    # that valid value must not be reported as schema-violating.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/{z}/{x}/{y}.png": {
                "get": {
                    "parameters": [
                        {
                            "name": "z",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 2, "maximum": 6},
                        },
                        {
                            "name": "x",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 0, "maximum": 63},
                        },
                        {
                            "name": "y",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 0, "maximum": 63},
                        },
                    ],
                    "responses": {"200": {"description": "OK"}, "422": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/<z>/<x>/<y>")
    def tile(z, x, y):
        return jsonify({"ok": True})

    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_call(context, case, **kwargs):
    pp = case.path_parameters
    for name in ("x", "y"):
        try:
            pp[name] = min(max(int(pp[name]), 0), 63)
        except (KeyError, TypeError, ValueError):
            pp[name] = 0
    try:
        z = int(pp["z"])
    except (KeyError, TypeError, ValueError):
        z = None
    if z is None or not 2 <= z <= 6:
        pp["z"] = 2
        """
    )

    result = cli.run_openapi_app(
        app,
        "--checks=negative_data_rejection",
        "--phases=coverage",
        "--continue-on-failure",
        hooks=module,
    )
    assert "Schema-violating request accepted" not in result.stdout, result.stdout
    assert result.exit_code == ExitCode.OK, result.stdout


def test_hook_editing_headers_keeps_captured_body_positive(ctx, cli):
    # GH-5105: a negative-mode draw that reuses a captured id is a valid body; a header edit must not flip it.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/branches": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name"],
                                    "additionalProperties": False,
                                    "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 10}},
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
                                        "required": ["id"],
                                        "additionalProperties": False,
                                        "properties": {"id": {"type": "string", "format": "uuid"}},
                                    }
                                }
                            },
                        },
                        "422": {"description": "Invalid"},
                    },
                }
            },
            "/sessions": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["branchId"],
                                    "additionalProperties": False,
                                    "properties": {"branchId": {"type": "string", "format": "uuid"}},
                                }
                            }
                        },
                    },
                    "responses": {"201": {"description": "Created"}, "422": {"description": "Invalid"}},
                }
            },
        }
    )

    @app.route("/branches", methods=["POST"])
    def create_branch():
        body = request.get_json(silent=True)
        name = body.get("name") if isinstance(body, dict) else None
        if set(body or ()) == {"name"} and isinstance(name, str) and 1 <= len(name) <= 10:
            return jsonify({"id": str(uuid.uuid4())}), 201
        return jsonify({}), 422

    @app.route("/sessions", methods=["POST"])
    def create_session():
        body = request.get_json(silent=True)
        branch_id = body.get("branchId") if isinstance(body, dict) else None
        if set(body or ()) == {"branchId"} and isinstance(branch_id, str) and UUID_PATTERN.match(branch_id):
            return jsonify({}), 201
        return jsonify({}), 422

    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_call(context, case, kwargs):
    if case.operation.label == "POST /sessions" and not case.meta.generation.mode.is_negative:
        case.headers = {**(case.headers or {}), "X-Request-Id": "trace"}
        """
    )

    result = cli.run_openapi_app(
        app,
        "--checks=negative_data_rejection",
        "--generation-deterministic",
        "--phases=fuzzing",
        "--max-examples=10",
        hooks=module,
    )
    assert "Schema-violating request accepted" not in result.stdout, result.stdout
    assert result.exit_code == ExitCode.OK, result.stdout


@pytest.mark.parametrize(
    ("replacement", "accepted_invalid"),
    [("5", False), ("five", True)],
    ids=["numeric-string", "non-numeric-string"],
)
def test_hook_writing_path_identifier_as_string(ctx, cli, replacement, accepted_invalid):
    # The pool feeds `4` from the list response; the hook redirects it, as text, to another user.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "required": ["id"],
                                            "properties": {"id": {"type": "integer"}},
                                        },
                                    }
                                }
                            },
                        }
                    }
                }
            },
            "/users/{id}/lock": {
                "delete": {
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                }
            },
        }
    )

    @app.route("/users", methods=["GET"])
    def list_users():
        return jsonify([{"id": 4}])

    @app.route("/users/<id>/lock", methods=["DELETE"])
    def unlock_user(id):
        if id in ("4", "5", "five"):
            return jsonify({}), 200
        return jsonify({}), 404

    module = ctx.write_pymodule(
        f"""
@schemathesis.hook
def before_call(context, case, kwargs):
    if case.operation.label == "DELETE /users/{{id}}/lock" and case.path_parameters.get("id") == 4:
        case.path_parameters["id"] = {replacement!r}
        """
    )

    result = cli.run_openapi_app(
        app,
        "--checks=negative_data_rejection",
        "--generation-deterministic",
        "--phases=fuzzing",
        "--mode=all",
        "--max-examples=10",
        hooks=module,
    )
    assert ("Schema-violating request accepted" in result.stdout) is accepted_invalid, result.stdout
