import json

import pytest
from _pytest.main import ExitCode
from flask import jsonify, request

import schemathesis
from schemathesis.engine import Status, events


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


def test_negative_metadata_required_property(ctx, cli, app_runner):
    app, _ = ctx.openapi.make_flask_app(
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

    @app.route("/items", methods=["POST"])
    def create_item():
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
