from __future__ import annotations

import re

import pytest
from fastapi import FastAPI, HTTPException
from flask import jsonify, request
from pydantic import BaseModel, Field

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


PATTERN_FIELDS: tuple[tuple[str, str], ...] = (
    ("code", "[A-Z]{2,5}"),
    ("ticker", "[A-Z]{3,4}"),
    ("zip_code", "[0-9]{5}"),
)


@pytest.fixture
def pattern_planted_bug_app(ctx, app_runner):
    # Multiple `@Pattern`-bounded fields so observations cross the threshold during coverage.
    schema_body = {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {field: {"type": "string"} for field, _ in PATTERN_FIELDS}
                        | {"tags": {"type": "array", "items": {"type": "string"}}},
                        "required": [field for field, _ in PATTERN_FIELDS],
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
            f'{field} - must match "{regex}"'
            for field, regex in PATTERN_FIELDS
            if not isinstance(body.get(field), str) or not re.fullmatch(regex, body[field])
        ]
        if issues:
            return jsonify({"messages": issues}), 400
        return "", 500

    port = app_runner.run_flask_app(app)
    return f"http://127.0.0.1:{port}/openapi.json"


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_pattern(cli, pattern_planted_bug_app, snapshot_cli):
    assert (
        cli.run(
            pattern_planted_bug_app,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


# One field per carrier key so observations for all three cross the threshold during coverage.
# Each entry pairs a Jackson type name (used in the message text) with the
# JSON-Schema format the consumer adjustment maps it to (used to gate acceptance).
JACKSON_TYPED_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("hire_date", "java.time.LocalDate", "msg", "date"),
    ("started_at", "java.time.LocalDateTime", "message", "date-time"),
    ("token", "java.util.UUID", "error", "uuid"),
)


@pytest.fixture
def jackson_planted_bug_app(ctx, app_runner):
    # Multiple Jackson-typed fields so observations cross the threshold during coverage.
    schema_body = {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {field: {"type": "string"} for field, _, _, _ in JACKSON_TYPED_FIELDS}
                        | {"tags": {"type": "array", "items": {"type": "string"}}},
                        "required": [field for field, _, _, _ in JACKSON_TYPED_FIELDS],
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
            return jsonify({"msg": "JSON parse error"}), 400
        envelope: dict[str, str] = {}
        for field, java_type, carrier_key, format_name in JACKSON_TYPED_FIELDS:
            value = body.get(field)
            if not is_valid(value, {"type": "string", "format": format_name}):
                envelope[carrier_key] = (
                    f"JSON parse error: Cannot deserialize value of type "
                    f'`{java_type}` from String "{value}" through reference chain: User["{field}"]'
                )
        if envelope:
            return jsonify(envelope), 400
        return "", 500

    port = app_runner.run_flask_app(app)
    return f"http://127.0.0.1:{port}/openapi.json"


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_type_mismatch(cli, jackson_planted_bug_app, snapshot_cli):
    assert (
        cli.run(
            jackson_planted_bug_app,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


# One enum-typed field per carrier key so observations cross the threshold during coverage.
ENUM_TYPED_FIELDS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("user_type", "com.example.UserType", "msg", ("USER", "ADMIN")),
    ("status", "com.example.Status", "message", ("PENDING", "ACTIVE", "ARCHIVED")),
    ("priority", "com.example.Priority", "error", ("LOW", "HIGH")),
)


@pytest.fixture
def jackson_enum_planted_bug_app(ctx, app_runner):
    schema_body = {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {field: {"type": "string"} for field, _, _, _ in ENUM_TYPED_FIELDS}
                        | {"tags": {"type": "array", "items": {"type": "string"}}},
                        "required": [field for field, _, _, _ in ENUM_TYPED_FIELDS],
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
            return jsonify({"msg": "JSON parse error"}), 400
        envelope: dict[str, str] = {}
        for field, java_type, carrier_key, accepted in ENUM_TYPED_FIELDS:
            value = body.get(field)
            if value not in accepted:
                envelope[carrier_key] = (
                    f"JSON parse error: Cannot deserialize value of type "
                    f'`{java_type}` from String "{value}": '
                    f"not one of the values accepted for Enum class: [{', '.join(accepted)}] "
                    f'through reference chain: User["{field}"]'
                )
        if envelope:
            return jsonify(envelope), 400
        return "", 500

    port = app_runner.run_flask_app(app)
    return f"http://127.0.0.1:{port}/openapi.json"


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_enum(cli, jackson_enum_planted_bug_app, snapshot_cli):
    assert (
        cli.run(
            jackson_enum_planted_bug_app,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


# Required query parameters not declared as such in the spec — Spring rejects
# their absence with `MissingServletRequestParameterException`. Multiple params
# so observations cross the calibration threshold during coverage.
MISSING_QUERY_PARAMS: tuple[str, ...] = ("lat", "lon", "raioMaximo")


@pytest.fixture
def missing_query_param_app(ctx, app_runner):
    schema_body = {
        "parameters": [{"name": name, "in": "query", "schema": {"type": "number"}} for name in MISSING_QUERY_PARAMS],
        "responses": {
            "400": {"description": "Bad"},
            "500": {"description": "Server Error"},
        },
    }
    app, _ = ctx.openapi.make_flask_app({"/v1/hospitais/maisProximo": {"get": dict(schema_body)}})

    @app.route("/v1/hospitais/maisProximo", methods=["GET"])
    def find_nearest():
        absent = [name for name in MISSING_QUERY_PARAMS if name not in request.args]
        if absent:
            return jsonify(
                {
                    "timestamp": "2026-05-01T01:00:40.560+0000",
                    "status": 400,
                    "error": "Bad Request",
                    "message": "; ".join(f"Required Double parameter '{name}' is not present" for name in absent),
                    "path": "/v1/hospitais/maisProximo",
                }
            ), 400
        return "", 500  # planted bug — surfaces once all three params are required.

    port = app_runner.run_flask_app(app)
    return f"http://127.0.0.1:{port}/openapi.json"


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_missing_query_parameter(cli, missing_query_param_app, snapshot_cli):
    assert (
        cli.run(
            missing_query_param_app,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


class CreateUser(BaseModel):
    name: str = Field(min_length=3, max_length=8)
    code: str = Field(min_length=5, max_length=20)
    nickname: str = Field(min_length=10, max_length=50)


# Constraint keywords removed from the served schema to mimic generator drift.
_DROPPED_KEYWORDS = (
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "pattern",
    "enum",
)


def _drop_constraints(node: object) -> None:
    if isinstance(node, dict):
        for keyword in _DROPPED_KEYWORDS:
            node.pop(keyword, None)
        for value in node.values():
            _drop_constraints(value)
    elif isinstance(node, list):
        for item in node:
            _drop_constraints(item)


@pytest.fixture
def pydantic_planted_bug_app(app_runner):
    # Mirror generator drift: the model still enforces its constraints,
    # the served schema has them dropped (e.g. Pydantic 2.10 HttpUrl).
    app = FastAPI()

    @app.post("/users")
    def create(user: CreateUser):
        raise HTTPException(500)

    original_openapi = app.openapi

    def drifted_openapi() -> dict:
        schema = original_openapi()
        _drop_constraints(schema.get("components", {}).get("schemas", {}))
        return schema

    app.openapi = drifted_openapi  # type: ignore[method-assign]  # FastAPI overrides via attribute assignment
    port = app_runner.run_asgi_app(app)
    return f"http://127.0.0.1:{port}/openapi.json"


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_recovers_constraints_dropped_from_pydantic_schema(cli, pydantic_planted_bug_app, snapshot_cli):
    assert (
        cli.run(
            pydantic_planted_bug_app,
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
            "--seed=100",
        )
        == snapshot_cli
    )
