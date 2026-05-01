from __future__ import annotations

import pytest
from flask import jsonify, request

from schemathesis.core.error_feedback.pipeline import _reset_pipeline_for_tests
from schemathesis.core.jsonschema import is_valid

REQUIRED_FIELDS = ("email", "username", "password")


@pytest.fixture(autouse=True)
def _reset_feedback_pipeline():
    # MRU singleton leaks across tests otherwise.
    _reset_pipeline_for_tests()


@pytest.fixture
def planted_bug_app(ctx, app_runner):
    # 4xx gate hides a planted 500 until feedback mutates the schema.
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
    # Multiple bounded fields so observations cross the calibration threshold during coverage.
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


FORMAT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("email", "email", "must be a well-formed email address"),
    ("website", "uri", "must be a valid URL"),
    ("token", "uuid", "must be a valid UUID"),
)


@pytest.fixture
def format_planted_bug_app(ctx, app_runner):
    # Multiple format-bounded fields so observations cross the threshold during coverage.
    schema_body = {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {field: {"type": "string"} for field, _, _ in FORMAT_FIELDS}
                        | {"tags": {"type": "array", "items": {"type": "string"}}},
                        "required": [field for field, _, _ in FORMAT_FIELDS],
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
            f"{field} - {message}"
            for field, format_name, message in FORMAT_FIELDS
            if not is_valid(body.get(field), {"type": "string", "format": format_name})
        ]
        if issues:
            return jsonify({"messages": issues}), 400
        return "", 500

    port = app_runner.run_flask_app(app)
    return f"http://127.0.0.1:{port}/openapi.json"


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_format(cli, format_planted_bug_app, snapshot_cli):
    assert (
        cli.run(
            format_planted_bug_app,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


NUMERIC_BOUNDED_FIELDS: tuple[tuple[str, str, float, str], ...] = (
    # field, message, server-bound, schema-type
    ("score", "must be greater than or equal to 0", 0.0, "integer"),
    ("rating", "must be less than or equal to 5", 5.0, "integer"),
    ("price", "must be greater than 0", 0.0, "number"),
)


@pytest.fixture
def numeric_bound_planted_bug_app(ctx, app_runner):
    # Multiple bounded numeric fields so observations cross the threshold during coverage.
    schema_body = {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "score": {"type": "integer"},
                            "rating": {"type": "integer"},
                            "price": {"type": "number"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["score", "rating", "price"],
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
        score = body.get("score")
        rating = body.get("rating")
        price = body.get("price")
        issues = []
        if not isinstance(score, int) or isinstance(score, bool) or score < 0:
            issues.append(f"score - {NUMERIC_BOUNDED_FIELDS[0][1]}")
        if not isinstance(rating, int) or isinstance(rating, bool) or rating > 5:
            issues.append(f"rating - {NUMERIC_BOUNDED_FIELDS[1][1]}")
        if not isinstance(price, (int, float)) or isinstance(price, bool) or price <= 0:
            issues.append(f"price - {NUMERIC_BOUNDED_FIELDS[2][1]}")
        if issues:
            return jsonify({"messages": issues}), 400
        return "", 500

    port = app_runner.run_flask_app(app)
    return f"http://127.0.0.1:{port}/openapi.json"


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_numeric_bound(cli, numeric_bound_planted_bug_app, snapshot_cli):
    assert (
        cli.run(
            numeric_bound_planted_bug_app,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )
