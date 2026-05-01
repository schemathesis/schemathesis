from __future__ import annotations

import pytest
from flask import jsonify, request

from schemathesis.core.error_feedback.pipeline import _reset_pipeline_for_tests

REQUIRED_FIELDS = ("email", "username", "password")


@pytest.fixture(autouse=True)
def _reset_feedback_pipeline():
    # MRU singleton leaks across tests otherwise.
    _reset_pipeline_for_tests()


@pytest.fixture
def planted_bug_app(ctx, app_runner):
    # Server requires email/username/password — fields the schema below never declares.
    # The 4xx gate hides a planted 500: schemathesis sees only "API rejected schema-compliant
    # request" until feedback mutates the body schema to satisfy the gate.
    schema_body = {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "active": {"type": "boolean"},
                        },
                    }
                }
            },
        },
        "responses": {
            "400": {"description": "Bad"},
            "500": {"description": "Server Error"},
        },
    }
    app, _ = ctx.openapi.make_flask_app({"/users": {"post": dict(schema_body)}})

    @app.route("/users", methods=["POST"])
    def create_user():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"messages": ["Please provide Request Body in valid JSON format"]}), 400
        missing = [
            f"{name} - must not be blank"
            for name in REQUIRED_FIELDS
            if not isinstance(body.get(name), str) or not body[name].strip()
        ]
        if missing:
            return jsonify({"messages": missing}), 400
        return "", 500

    port = app_runner.run_flask_app(app)
    return f"http://127.0.0.1:{port}/openapi.json"


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize(
    "extra_kwargs",
    [
        {},
        {"config": {"phases": {"fuzzing": {"error-feedback": {"enabled": False}}}}},
    ],
    ids=["enabled", "disabled"],
)
def test_feedback_toggles_planted_bug_visibility(cli, planted_bug_app, snapshot_cli, extra_kwargs):
    assert (
        cli.run(
            planted_bug_app,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
            **extra_kwargs,
        )
        == snapshot_cli
    )


@pytest.fixture
def nested_planted_bug_app(ctx, app_runner):
    # Server demands `contact.email`, fed back via Spring's dotted-path message shape.
    schema_body = {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "contact": {"type": "object", "properties": {}},
                        },
                    }
                }
            },
        },
        "responses": {
            "201": {"description": "OK"},
            "400": {"description": "Bad"},
            "500": {"description": "Server Error"},
        },
    }
    app, _ = ctx.openapi.make_flask_app({"/profiles": {"post": dict(schema_body)}})

    @app.route("/profiles", methods=["POST"])
    def create_profile():
        body = request.get_json(silent=True)
        contact = body.get("contact") if isinstance(body, dict) else None
        email = contact.get("email") if isinstance(contact, dict) else None
        if not isinstance(email, str) or not email.strip():
            return jsonify({"messages": ["contact.email - must not be blank"]}), 400
        return "", 500

    port = app_runner.run_flask_app(app)
    return f"http://127.0.0.1:{port}/openapi.json"


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_dotted_path(cli, nested_planted_bug_app, snapshot_cli):
    assert (
        cli.run(
            nested_planted_bug_app,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


SIZE_BOUNDED_FIELDS = (
    ("username", 3, 8),
    ("title", 5, 20),
    ("description", 10, 50),
)


@pytest.fixture
def size_bound_planted_bug_app(ctx, app_runner):
    # Server enforces length bounds the schema doesn't declare. Multiple bounded
    # fields so each rejected case emits one message per field — enough observations
    # cross the calibration threshold by the time fuzzing builds its strategy.
    schema_body = {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "username": {"type": "string"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": [field for field, _, _ in SIZE_BOUNDED_FIELDS],
                    }
                }
            },
        },
        "responses": {
            "400": {"description": "Bad"},
            "500": {"description": "Server Error"},
        },
    }
    app, _ = ctx.openapi.make_flask_app({"/users": {"post": dict(schema_body)}})

    @app.route("/users", methods=["POST"])
    def create_user():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"messages": ["Please provide Request Body in valid JSON format"]}), 400
        issues = [
            f"{field} - size must be between {lo} and {hi}"
            for field, lo, hi in SIZE_BOUNDED_FIELDS
            if not isinstance(body.get(field), str) or not lo <= len(body[field]) <= hi
        ]
        if issues:
            return jsonify({"messages": issues}), 400
        return "", 500

    port = app_runner.run_flask_app(app)
    return f"http://127.0.0.1:{port}/openapi.json"


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_size_bound(cli, size_bound_planted_bug_app, snapshot_cli):
    assert (
        cli.run(
            size_bound_planted_bug_app,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )
