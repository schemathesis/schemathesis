from __future__ import annotations

import re
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from flask import jsonify, request
from pydantic import BaseModel, Field

import schemathesis
from schemathesis.core.error_feedback.pipeline import _reset_pipeline_for_tests
from schemathesis.core.jsonschema import is_valid
from schemathesis.engine import events, from_schema
from schemathesis.engine.run import PhaseName
from schemathesis.generation import GenerationMode

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
        for field, type_name, carrier_key, format_name in JACKSON_TYPED_FIELDS:
            value = body.get(field)
            if not is_valid(value, {"type": "string", "format": format_name}):
                envelope[carrier_key] = (
                    f"JSON parse error: Cannot deserialize value of type "
                    f'`{type_name}` from String "{value}" through reference chain: User["{field}"]'
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


@pytest.fixture
def jackson_planted_bug_app_ref_bundled(ctx, app_runner):
    # Body schema references `components.schemas.User`, which itself $refs another component —
    # this two-hop chain is what schemathesis's loader turns into the `x-bundled` form
    # observed on real Spring/PTS specs.
    address_schema = {"type": "object", "properties": {"street": {"type": "string"}}}
    user_schema = {
        "type": "object",
        "properties": {field: {"type": "string"} for field, _, _, _ in JACKSON_TYPED_FIELDS}
        | {"address": {"$ref": "#/components/schemas/Address"}},
        "required": [field for field, _, _, _ in JACKSON_TYPED_FIELDS],
    }
    paths = {
        "/users": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/User"}}},
                },
                "responses": {"400": {"description": "Bad"}, "500": {"description": "Server Error"}},
            }
        }
    }
    app, _ = ctx.openapi.make_flask_app(paths, components={"schemas": {"User": user_schema, "Address": address_schema}})

    @app.route("/users", methods=["POST"])
    def create_user():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"msg": "JSON parse error"}), 400
        envelope: dict[str, str] = {}
        for field, type_name, carrier_key, format_name in JACKSON_TYPED_FIELDS:
            value = body.get(field)
            if not is_valid(value, {"type": "string", "format": format_name}):
                envelope[carrier_key] = (
                    f"JSON parse error: Cannot deserialize value of type "
                    f'`{type_name}` from String "{value}" through reference chain: User["{field}"]'
                )
        if envelope:
            return jsonify(envelope), 400
        return "", 500

    port = app_runner.run_flask_app(app)
    return f"http://127.0.0.1:{port}/openapi.json"


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_type_mismatch_ref_bundled(
    cli, jackson_planted_bug_app_ref_bundled, snapshot_cli
):
    # Adjustment must reach the body schema even when bundled behind $ref / x-bundled.
    assert (
        cli.run(
            jackson_planted_bug_app_ref_bundled,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


# Multiple int fields so overflow observations cross the calibration threshold during coverage.
JACKSON_OVERFLOW_FIELDS: tuple[tuple[str, str], ...] = (
    ("availableBeds", "msg"),
    ("quantity", "message"),
    ("score", "error"),
)
_INT32_MIN = -2_147_483_648
_INT32_MAX = 2_147_483_647


@pytest.fixture
def jackson_overflow_planted_bug_app(ctx, app_runner):
    schema_body = {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {field: {"type": "integer"} for field, _ in JACKSON_OVERFLOW_FIELDS},
                        "required": [field for field, _ in JACKSON_OVERFLOW_FIELDS],
                    }
                }
            },
        },
        "responses": {
            "400": {"description": "Bad"},
            "500": {"description": "Server Error"},
        },
    }
    app, _ = ctx.openapi.make_flask_app({"/hospitals": {"post": dict(schema_body)}})

    @app.route("/hospitals", methods=["POST"])
    def create_hospital():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"msg": "JSON parse error"}), 400
        envelope: dict[str, str] = {}
        for field, carrier_key in JACKSON_OVERFLOW_FIELDS:
            value = body.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and not _INT32_MIN <= value <= _INT32_MAX:
                # Fixed placeholder for the actual value keeps the snapshot stable
                # across runs; the parser regex accepts any non-paren content.
                envelope[carrier_key] = (
                    "JSON parse error: Numeric value (X) out of range of int; "
                    "nested exception is com.fasterxml.jackson.databind.JsonMappingException: "
                    "Numeric value (X) out of range of int "
                    f'(through reference chain: HospitalDTO["{field}"])'
                )
        if envelope:
            return jsonify(envelope), 400
        return "", 500

    port = app_runner.run_flask_app(app)
    return f"http://127.0.0.1:{port}/openapi.json"


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_jackson_numeric_overflow(cli, jackson_overflow_planted_bug_app, snapshot_cli):
    assert (
        cli.run(
            jackson_overflow_planted_bug_app,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
            "--seed=42",
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
        for field, type_name, carrier_key, accepted in ENUM_TYPED_FIELDS:
            value = body.get(field)
            if value not in accepted:
                envelope[carrier_key] = (
                    f"JSON parse error: Cannot deserialize value of type "
                    f'`{type_name}` from String "{value}": '
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


_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def test_stale_example_evicted_after_format_inference(ctx, app_runner):
    paths = {
        "/events": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["commitDate"],
                                "properties": {"commitDate": {"type": "string"}},
                                "example": {"commitDate": "dd-MM-yyyy"},
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad Request"}},
            }
        }
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/events", methods=["POST"])
    def create_event():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"msg": "JSON parse error"}), 400
        value = body.get("commitDate")
        if not isinstance(value, str) or not _ISO_DATETIME.match(value):
            return jsonify(
                {
                    "msg": (
                        f"JSON parse error: Cannot deserialize value of type `java.time.LocalDateTime` "
                        f'from String "{value}" through reference chain: Event["commitDate"]'
                    )
                }
            ), 400
        return "", 200

    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["coverage", "fuzzing"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=100)

    fuzzing_commit_dates: list[str] = []
    for event in from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished) and event.phase == PhaseName.FUZZING:
            for case_node in event.recorder.cases.values():
                body = case_node.value.body
                if isinstance(body, dict) and isinstance(body.get("commitDate"), str):
                    fuzzing_commit_dates.append(body["commitDate"])

    assert fuzzing_commit_dates, "No fuzzing body draws collected"
    stale = [v for v in fuzzing_commit_dates if v == "dd-MM-yyyy"]
    assert not stale, f"Stale `commitDate`: {len(stale)}/{len(fuzzing_commit_dates)} fuzzing draws"


def test_stale_example_evicted_after_format_inference_on_query_param(ctx, app_runner):
    paths = {
        "/items": {
            "get": {
                "parameters": [
                    {
                        "name": "token",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                        "example": "NOT_A_UUID",
                    }
                ],
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad Request"}},
            }
        }
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/items", methods=["GET"])
    def items():
        value = request.args.get("token", "")
        try:
            uuid.UUID(value)
        except (ValueError, AttributeError):
            return jsonify(
                {
                    "detail": (
                        "Method parameter 'token': Failed to convert value of type "
                        "'java.lang.String' to required type 'java.util.UUID'"
                    )
                }
            ), 400
        return "", 200

    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["examples", "coverage", "fuzzing"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=100)

    fuzzing_token_values: list[str] = []
    for event in from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished) and event.phase == PhaseName.FUZZING:
            for case_node in event.recorder.cases.values():
                query = case_node.value.query
                if isinstance(query, dict) and isinstance(query.get("token"), str):
                    fuzzing_token_values.append(query["token"])

    assert fuzzing_token_values, "No fuzzing query draws collected"
    stale = [v for v in fuzzing_token_values if v == "NOT_A_UUID"]
    assert not stale, f"Stale `token`: {len(stale)}/{len(fuzzing_token_values)} fuzzing draws"


_STALE_DATES = ("dd-MM-yyyy", "MM/dd/yyyy")
_STALE_TOKENS = ("NOT_A_UUID_1", "NOT_A_UUID_2")


def test_stale_body_example_evicted_in_coverage_after_examples_observations(ctx, app_runner):
    paths = {
        "/events": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["commitDate"],
                                "properties": {"commitDate": {"type": "string"}},
                            },
                            "examples": {
                                "alpha": {"value": {"commitDate": _STALE_DATES[0]}},
                                "beta": {"value": {"commitDate": _STALE_DATES[1]}},
                            },
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad Request"}},
            }
        }
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/events", methods=["POST"])
    def create_event():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"msg": "JSON parse error"}), 400
        value = body.get("commitDate")
        if not isinstance(value, str) or not _ISO_DATETIME.match(value):
            return jsonify(
                {
                    "msg": (
                        f"JSON parse error: Cannot deserialize value of type `java.time.LocalDateTime` "
                        f'from String "{value}" through reference chain: Event["commitDate"]'
                    )
                }
            ), 400
        return "", 200

    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["examples", "coverage"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=20)

    coverage_commit_dates: list[str] = []
    for event in from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished) and event.phase == PhaseName.COVERAGE:
            for case_node in event.recorder.cases.values():
                body = case_node.value.body
                if isinstance(body, dict) and isinstance(body.get("commitDate"), str):
                    coverage_commit_dates.append(body["commitDate"])

    assert coverage_commit_dates, "No coverage body draws collected"
    stale = [v for v in coverage_commit_dates if v in _STALE_DATES]
    assert not stale, f"Stale `commitDate`: {len(stale)}/{len(coverage_commit_dates)} coverage draws"


def test_stale_query_example_evicted_in_coverage_after_examples_observations(ctx, app_runner):
    paths = {
        "/items": {
            "get": {
                "parameters": [
                    {
                        "name": "token",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                        "examples": {
                            "alpha": {"value": _STALE_TOKENS[0]},
                            "beta": {"value": _STALE_TOKENS[1]},
                        },
                    }
                ],
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad Request"}},
            }
        }
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/items", methods=["GET"])
    def items():
        value = request.args.get("token", "")
        try:
            uuid.UUID(value)
        except (ValueError, AttributeError):
            return jsonify(
                {
                    "detail": (
                        "Method parameter 'token': Failed to convert value of type "
                        "'java.lang.String' to required type 'java.util.UUID'"
                    )
                }
            ), 400
        return "", 200

    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["examples", "coverage"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=20)

    coverage_token_values: list[str] = []
    for event in from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished) and event.phase == PhaseName.COVERAGE:
            for case_node in event.recorder.cases.values():
                query = case_node.value.query
                if isinstance(query, dict) and isinstance(query.get("token"), str):
                    coverage_token_values.append(query["token"])

    assert coverage_token_values, "No coverage query draws collected"
    stale = [v for v in coverage_token_values if v in _STALE_TOKENS]
    assert not stale, f"Stale `token`: {len(stale)}/{len(coverage_token_values)} coverage draws"


def test_stateful_body_generation_consumes_format_inferred_during_fuzzing(ctx, app_runner):
    paths = {
        "/events": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["commitDate"],
                                "properties": {"commitDate": {"type": "string"}},
                            },
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {"schema": {"type": "object", "properties": {"id": {"type": "string"}}}}
                        },
                        "links": {
                            "GetEventById": {
                                "operationId": "getEvent",
                                "parameters": {"eventId": "$response.body#/id"},
                            }
                        },
                    },
                    "400": {"description": "Bad Request"},
                },
            }
        },
        "/events/{eventId}": {
            "get": {
                "operationId": "getEvent",
                "parameters": [{"name": "eventId", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "OK"}, "404": {"description": "Not Found"}},
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/events", methods=["POST"])
    def create_event():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"msg": "JSON parse error"}), 400
        value = body.get("commitDate")
        if not isinstance(value, str) or not _ISO_DATETIME.match(value):
            return jsonify(
                {
                    "msg": (
                        f"JSON parse error: Cannot deserialize value of type `java.time.LocalDateTime` "
                        f'from String "{value}" through reference chain: Event["commitDate"]'
                    )
                }
            ), 400
        return jsonify({"id": "evt-1"}), 200

    @app.route("/events/<event_id>", methods=["GET"])
    def get_event(event_id: str):
        return "", 200

    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["fuzzing", "stateful"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=50)

    stateful_commit_dates: list[str] = []
    for event in from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished) and event.phase == PhaseName.STATEFUL_TESTING:
            for case_node in event.recorder.cases.values():
                body = case_node.value.body
                if isinstance(body, dict) and isinstance(body.get("commitDate"), str):
                    stateful_commit_dates.append(body["commitDate"])

    assert stateful_commit_dates, "No stateful POST body draws collected"
    iso_matches = [v for v in stateful_commit_dates if _ISO_DATETIME.match(v)]
    assert iso_matches, (
        f"None of {len(stateful_commit_dates)} stateful `commitDate` draws match the inferred date-time format; "
        f"first few: {stateful_commit_dates[:5]}"
    )


# The Rails parser must handle three envelope shapes for the same underlying
# observations. Each parametrisation plants the same bug behind a Rails-shaped
# size-bound gate but emits the response in a different envelope. The bug is
# only reached if the parser learned the size bound from that particular shape.
RAILS_BOUNDED_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("username", 3, 30),
    ("title", 5, 80),
    ("description", 10, 200),
)


def _rails_modern_envelope(issues: list[tuple[str, str]]) -> tuple[dict, int]:
    body: dict[str, list[str]] = {}
    for field, message in issues:
        body.setdefault(field, []).append(message)
    return body, 422


def _rails_legacy_envelope(issues: list[tuple[str, str]]) -> tuple[dict, int]:
    return {"errors": [f"{field.replace('_', ' ').capitalize()} {msg}" for field, msg in issues]}, 422


def _rails_wrapped_envelope(issues: list[tuple[str, str]]) -> tuple[dict, int]:
    body: dict[str, list[str]] = {}
    for field, message in issues:
        body.setdefault(field, []).append(message)
    return {"errors": body}, 422


_RAILS_ENVELOPES = {
    "modern": _rails_modern_envelope,
    "legacy": _rails_legacy_envelope,
    "wrapped": _rails_wrapped_envelope,
}


@pytest.fixture
def rails_planted_bug_app_factory(ctx, app_runner):
    # Each parametrisation gets its own server emitting one of the three
    # Rails envelope shapes. The 422 gate uses the Rails
    # `is too short (minimum is N characters)` phrasing so the parser can
    # infer a length constraint and reach the planted 500.
    def build(envelope_name: str) -> str:
        envelope = _RAILS_ENVELOPES[envelope_name]
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
                            "required": [field for field, _, _ in RAILS_BOUNDED_FIELDS],
                        }
                    }
                },
            },
            "responses": {
                "422": {"description": "Unprocessable Entity"},
                "500": {"description": "Server Error"},
            },
        }
        app, _ = ctx.openapi.make_flask_app({"/users": {"post": dict(schema_body)}})

        @app.route("/users", methods=["POST"])
        def create_user():
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                payload, status = envelope([("base", "must be a valid JSON object")])
                return jsonify(payload), status
            issues: list[tuple[str, str]] = []
            for field, lo, hi in RAILS_BOUNDED_FIELDS:
                value = body.get(field, "")
                if not isinstance(value, str):
                    value = ""
                if len(value) < lo:
                    issues.append((field, f"is too short (minimum is {lo} characters)"))
                elif len(value) > hi:
                    issues.append((field, f"is too long (maximum is {hi} characters)"))
            if issues:
                payload, status = envelope(issues)
                return jsonify(payload), status
            return "", 500

        port = app_runner.run_flask_app(app)
        return f"http://127.0.0.1:{port}/openapi.json"

    return build


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize("envelope", sorted(_RAILS_ENVELOPES))
def test_feedback_unmasks_planted_bug_via_rails_envelope(cli, rails_planted_bug_app_factory, envelope, snapshot_cli):
    url = rails_planted_bug_app_factory(envelope)
    assert (
        cli.run(
            url,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


LARAVEL_BOUNDED_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("username", 3, 30),
    ("title", 5, 80),
    ("description", 10, 200),
)


@pytest.fixture
def laravel_planted_bug_app(ctx, app_runner):
    # 422 gate uses Laravel's `field must be at least N characters.` phrasing
    # so the parser can infer a size bound and reach the planted 500.
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
                        "required": [field for field, _, _ in LARAVEL_BOUNDED_FIELDS],
                    }
                }
            },
        },
        "responses": {
            "422": {"description": "Unprocessable Entity"},
            "500": {"description": "Server Error"},
        },
    }
    app, _ = ctx.openapi.make_flask_app({"/users": {"post": dict(schema_body)}})

    @app.route("/users", methods=["POST"])
    def create_user():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"message": "The given data was invalid.", "errors": {}}), 422
        errors: dict[str, list[str]] = {}
        for field, lo, hi in LARAVEL_BOUNDED_FIELDS:
            value = body.get(field, "")
            if not isinstance(value, str):
                value = ""
            if len(value) < lo:
                errors.setdefault(field, []).append(f"The {field} field must be at least {lo} characters.")
            elif len(value) > hi:
                errors.setdefault(field, []).append(f"The {field} field must not be greater than {hi} characters.")
        if errors:
            return jsonify({"message": "The given data was invalid.", "errors": errors}), 422
        return "", 500

    port = app_runner.run_flask_app(app)
    return f"http://127.0.0.1:{port}/openapi.json"


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_laravel_envelope(cli, laravel_planted_bug_app, snapshot_cli):
    assert (
        cli.run(
            laravel_planted_bug_app,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )
