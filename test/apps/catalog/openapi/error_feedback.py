from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from flask import Flask, jsonify, request
from pydantic import BaseModel, Field

from schemathesis.core.jsonschema import is_valid
from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.runtime import OpenAPIApp

ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
STALE_DATES: tuple[str, str] = ("dd-MM-yyyy", "MM/dd/yyyy")
STALE_TOKENS: tuple[str, str] = ("NOT_A_UUID_1", "NOT_A_UUID_2")
_TOKEN_CONVERSION_ERROR = (
    "Method parameter 'token': Failed to convert value of type 'java.lang.String' to required type 'java.util.UUID'"
)

_ASPNET_BOUNDED_FIELDS: tuple[tuple[str, str, int, int], ...] = (
    ("username", "Username", 3, 30),
    ("title", "Title", 5, 80),
    ("description", "Description", 10, 200),
)


def aspnet_planted_bug() -> OpenAPIApp:
    """Plant a 500 behind a ProblemDetails 400 gate using the DataAnnotations `minimum length of 'N'` phrasing."""
    spec = _build_aspnet_schema()
    app = make_flask_app_from_schema(spec)
    _register_aspnet_handlers(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def _build_aspnet_schema() -> dict:
    return build_schema(
        {
            "/users": {
                "post": {
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
                                    "required": [json_name for json_name, _, _, _ in _ASPNET_BOUNDED_FIELDS],
                                }
                            }
                        },
                    },
                    "responses": {
                        "400": {"description": "Bad Request"},
                        "500": {"description": "Server Error"},
                    },
                }
            }
        }
    )


def _problem_details(errors: dict[str, list[str]]) -> dict:
    return {
        "type": "https://tools.ietf.org/html/rfc9110#section-15.5.1",
        "title": "One or more validation errors occurred.",
        "status": 400,
        "errors": errors,
    }


def _register_aspnet_handlers(app: Flask) -> None:
    @app.route("/users", methods=["POST"])
    def create_user():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(_problem_details({})), 400
        errors: dict[str, list[str]] = {}
        for json_name, csharp_name, lo, hi in _ASPNET_BOUNDED_FIELDS:
            value = body.get(json_name, "")
            if not isinstance(value, str):
                value = ""
            if len(value) < lo:
                errors.setdefault(csharp_name, []).append(
                    f"The field {csharp_name} must be a string or array type with a minimum length of '{lo}'."
                )
            elif len(value) > hi:
                errors.setdefault(csharp_name, []).append(
                    f"The field {csharp_name} must be a string or array type with a maximum length of '{hi}'."
                )
        if errors:
            return jsonify(_problem_details(errors)), 400
        return "", 500


REQUIRED_FIELDS: tuple[str, ...] = ("email", "username", "password")


def planted_bug() -> OpenAPIApp:
    # 4xx required-fields gate hides a planted 500 until feedback fills the body.
    spec = build_schema(
        {
            "/users": {
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
            }
        }
    )
    app = make_flask_app_from_schema(spec)

    @app.route("/users", methods=["POST"])
    def create_user() -> Any:
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

    return OpenAPIApp(spec=spec, server=app, kind="flask")


def nested_planted_bug() -> OpenAPIApp:
    # 400 gate keyed on a dotted path (`contact.email`); feedback must lift the bound onto the nested object.
    spec = build_schema(
        {
            "/profiles": {
                "post": {
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
            }
        }
    )
    app = make_flask_app_from_schema(spec)

    @app.route("/profiles", methods=["POST"])
    def create_profile() -> Any:
        body = request.get_json(silent=True)
        contact = body.get("contact") if isinstance(body, dict) else None
        email = contact.get("email") if isinstance(contact, dict) else None
        if not isinstance(email, str) or not email.strip():
            return jsonify({"messages": ["contact.email - must not be blank"]}), 400
        return "", 500

    return OpenAPIApp(spec=spec, server=app, kind="flask")


SIZE_BOUNDED_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("username", 3, 8),
    ("title", 5, 20),
    ("description", 10, 50),
)


def size_bound_planted_bug() -> OpenAPIApp:
    # Multiple bounded fields so observations cross the calibration threshold during coverage.
    spec = build_schema(
        {
            "/users": {
                "post": {
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
            }
        }
    )
    app = make_flask_app_from_schema(spec)

    @app.route("/users", methods=["POST"])
    def create_user() -> Any:
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

    return OpenAPIApp(spec=spec, server=app, kind="flask")


FORMAT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("email", "email", "must be a well-formed email address"),
    ("website", "uri", "must be a valid URL"),
    ("token", "uuid", "must be a valid UUID"),
)


def format_planted_bug() -> OpenAPIApp:
    # Multiple format-bounded fields so observations cross the threshold during coverage.
    spec = build_schema(
        {
            "/users": {
                "post": {
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
            }
        }
    )
    app = make_flask_app_from_schema(spec)

    @app.route("/users", methods=["POST"])
    def create_user() -> Any:
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

    return OpenAPIApp(spec=spec, server=app, kind="flask")


NUMERIC_BOUNDED_FIELDS: tuple[tuple[str, str, float, str], ...] = (
    # field, message, server-bound, schema-type
    ("score", "must be greater than or equal to 0", 0.0, "integer"),
    ("rating", "must be less than or equal to 5", 5.0, "integer"),
    ("price", "must be greater than 0", 0.0, "number"),
)


def numeric_bound_planted_bug() -> OpenAPIApp:
    # Multiple bounded numeric fields so observations cross the threshold during coverage.
    spec = build_schema(
        {
            "/users": {
                "post": {
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
            }
        }
    )
    app = make_flask_app_from_schema(spec)

    @app.route("/users", methods=["POST"])
    def create_user() -> Any:
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

    return OpenAPIApp(spec=spec, server=app, kind="flask")


PATTERN_FIELDS: tuple[tuple[str, str], ...] = (
    ("code", "[A-Z]{2,5}"),
    ("ticker", "[A-Z]{3,4}"),
    ("zip_code", "[0-9]{5}"),
)


def pattern_planted_bug() -> OpenAPIApp:
    # Multiple `@Pattern`-bounded fields so observations cross the threshold during coverage.
    spec = build_schema(
        {
            "/users": {
                "post": {
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
            }
        }
    )
    app = make_flask_app_from_schema(spec)

    @app.route("/users", methods=["POST"])
    def create_user() -> Any:
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

    return OpenAPIApp(spec=spec, server=app, kind="flask")


# One field per carrier key so observations for all three cross the threshold during coverage.
# Each entry pairs a Jackson type name (used in the message text) with the
# JSON-Schema format the consumer adjustment maps it to (used to gate acceptance).
JACKSON_TYPED_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("hire_date", "java.time.LocalDate", "msg", "date"),
    ("started_at", "java.time.LocalDateTime", "message", "date-time"),
    ("token", "java.util.UUID", "error", "uuid"),
)


def _register_jackson_typed_handler(app: Flask) -> None:
    @app.route("/users", methods=["POST"])
    def create_user() -> Any:
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


def jackson_typed_planted_bug() -> OpenAPIApp:
    # Multiple Jackson-typed fields so observations cross the threshold during coverage.
    spec = build_schema(
        {
            "/users": {
                "post": {
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
            }
        }
    )
    app = make_flask_app_from_schema(spec)
    _register_jackson_typed_handler(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def jackson_typed_planted_bug_ref_bundled() -> OpenAPIApp:
    # Body schema references `components.schemas.User`, which itself $refs another component —
    # this two-hop chain is what schemathesis's loader turns into the `x-bundled` form
    # observed on real Spring specs.
    address_schema = {"type": "object", "properties": {"street": {"type": "string"}}}
    user_schema = {
        "type": "object",
        "properties": {field: {"type": "string"} for field, _, _, _ in JACKSON_TYPED_FIELDS}
        | {"address": {"$ref": "#/components/schemas/Address"}},
        "required": [field for field, _, _, _ in JACKSON_TYPED_FIELDS],
    }
    spec = build_schema(
        {
            "/users": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/User"}}},
                    },
                    "responses": {"400": {"description": "Bad"}, "500": {"description": "Server Error"}},
                }
            }
        },
        components={"schemas": {"User": user_schema, "Address": address_schema}},
    )
    app = make_flask_app_from_schema(spec)
    _register_jackson_typed_handler(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


_INT32_MIN = -2_147_483_648
_INT32_MAX = 2_147_483_647


def jackson_overflow_planted_bug() -> OpenAPIApp:
    spec = build_schema(
        {
            "/hospitals": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        # Spec-side example forces coverage to emit a deterministic
                                        # overflow value, so feedback always observes the parser's
                                        # bound and the snapshot stays stable across runs.
                                        "quantity": {"type": "integer", "example": 10_000_000_000},
                                        # Optional field so coverage produces multiple positive object
                                        # cases — observations on `quantity` cross the calibration
                                        # threshold before fuzzing builds its strategy.
                                        "note": {"type": "string"},
                                    },
                                    "required": ["quantity"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "400": {"description": "Bad"},
                        "500": {"description": "Server Error"},
                    },
                }
            }
        }
    )
    app = make_flask_app_from_schema(spec)

    @app.route("/hospitals", methods=["POST"])
    def create_hospital() -> Any:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"msg": "JSON parse error"}), 400
        value = body.get("quantity")
        if isinstance(value, int) and not isinstance(value, bool) and not _INT32_MIN <= value <= _INT32_MAX:
            # Fixed placeholder for the actual value keeps the snapshot stable across runs;
            # the parser regex accepts any non-paren content.
            return jsonify(
                {
                    "message": (
                        "JSON parse error: Numeric value (X) out of range of int; "
                        "nested exception is com.fasterxml.jackson.databind.JsonMappingException: "
                        "Numeric value (X) out of range of int "
                        '(through reference chain: HospitalDTO["quantity"])'
                    )
                }
            ), 400
        return "", 500

    return OpenAPIApp(spec=spec, server=app, kind="flask")


# One enum-typed field per carrier key so observations cross the threshold during coverage.
ENUM_TYPED_FIELDS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("user_type", "com.example.UserType", "msg", ("USER", "ADMIN")),
    ("status", "com.example.Status", "message", ("PENDING", "ACTIVE", "ARCHIVED")),
    ("priority", "com.example.Priority", "error", ("LOW", "HIGH")),
)


def jackson_enum_planted_bug() -> OpenAPIApp:
    spec = build_schema(
        {
            "/users": {
                "post": {
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
            }
        }
    )
    app = make_flask_app_from_schema(spec)

    @app.route("/users", methods=["POST"])
    def create_user() -> Any:
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

    return OpenAPIApp(spec=spec, server=app, kind="flask")


# Required query parameters not declared as such in the spec — Spring rejects
# their absence with `MissingServletRequestParameterException`. Multiple params
# so observations cross the calibration threshold during coverage.
MISSING_QUERY_PARAMS: tuple[str, ...] = ("lat", "lon", "raioMaximo")


def missing_query_param_planted_bug() -> OpenAPIApp:
    spec = build_schema(
        {
            "/v1/hospitais/maisProximo": {
                "get": {
                    "parameters": [
                        {"name": name, "in": "query", "schema": {"type": "number"}} for name in MISSING_QUERY_PARAMS
                    ],
                    "responses": {
                        "400": {"description": "Bad"},
                        "500": {"description": "Server Error"},
                    },
                }
            }
        }
    )
    app = make_flask_app_from_schema(spec)

    @app.route("/v1/hospitais/maisProximo", methods=["GET"])
    def find_nearest() -> Any:
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

    return OpenAPIApp(spec=spec, server=app, kind="flask")


class _PydanticUser(BaseModel):
    name: str = Field(min_length=3, max_length=8)
    code: str = Field(min_length=5, max_length=20)
    nickname: str = Field(min_length=10, max_length=50)
    # Optional field so coverage produces multiple positive object cases
    # (one per optional present/absent), letting the constraint observations
    # cross the calibration threshold before fuzzing builds its strategy.
    bio: str | None = None


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


def pydantic_planted_bug() -> OpenAPIApp:
    # Mirror generator drift: the model still enforces its constraints,
    # the served schema has them dropped (e.g. Pydantic 2.10 HttpUrl).
    app = FastAPI()

    @app.post("/users")
    def create(user: _PydanticUser) -> Any:
        raise HTTPException(500)

    original_openapi = app.openapi

    def drifted_openapi() -> dict:
        schema = original_openapi()
        _drop_constraints(schema.get("components", {}).get("schemas", {}))
        return schema

    app.openapi = drifted_openapi  # FastAPI overrides via attribute assignment
    return OpenAPIApp(spec=app.openapi(), server=app, kind="fastapi")


def _date_deserialize_error(value: object) -> dict[str, str]:
    return {
        "msg": (
            f"JSON parse error: Cannot deserialize value of type `java.time.LocalDateTime` "
            f'from String "{value}" through reference chain: Event["commitDate"]'
        )
    }


def _register_commit_date_handler(app: Flask, *, success_body: dict[str, str] | None = None) -> None:
    payload = success_body or {}

    @app.route("/events", methods=["POST"])
    def create_event() -> Any:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"msg": "JSON parse error"}), 400
        value = body.get("commitDate")
        if not isinstance(value, str) or not ISO_DATETIME.match(value):
            return jsonify(_date_deserialize_error(value)), 400
        return (jsonify(payload), 200) if payload else ("", 200)


def _commit_date_body_schema(*, example: object | None = None, examples: dict[str, dict] | None = None) -> dict:
    media: dict[str, Any] = {
        "schema": {
            "type": "object",
            "required": ["commitDate"],
            "properties": {"commitDate": {"type": "string"}},
        }
    }
    if example is not None:
        media["schema"]["example"] = example
    if examples is not None:
        media["examples"] = examples
    return {"required": True, "content": {"application/json": media}}


def commit_date_with_example() -> OpenAPIApp:
    """POST /events whose body schema carries a single `dd-MM-yyyy` example that fails the date format the server enforces."""
    spec = build_schema(
        {
            "/events": {
                "post": {
                    "requestBody": _commit_date_body_schema(example={"commitDate": "dd-MM-yyyy"}),
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Bad Request"}},
                }
            }
        }
    )
    app = make_flask_app_from_schema(spec)
    _register_commit_date_handler(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def commit_date_with_examples() -> OpenAPIApp:
    """POST /events with two named examples, both of which fail the server-side date format check."""
    spec = build_schema(
        {
            "/events": {
                "post": {
                    "requestBody": _commit_date_body_schema(
                        examples={
                            "alpha": {"value": {"commitDate": STALE_DATES[0]}},
                            "beta": {"value": {"commitDate": STALE_DATES[1]}},
                        }
                    ),
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Bad Request"}},
                }
            }
        }
    )
    app = make_flask_app_from_schema(spec)
    _register_commit_date_handler(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def commit_date_with_link() -> OpenAPIApp:
    """POST /events linked to GET /events/{eventId}; producer success returns `{id: evt-1}` for the consumer to follow."""
    spec = build_schema(
        {
            "/events": {
                "post": {
                    "requestBody": _commit_date_body_schema(),
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "string"}}}
                                }
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
    )
    app = make_flask_app_from_schema(spec)
    _register_commit_date_handler(app, success_body={"id": "evt-1"})

    @app.route("/events/<event_id>", methods=["GET"])
    def get_event(event_id: str) -> Any:
        return "", 200

    return OpenAPIApp(spec=spec, server=app, kind="flask")


def _register_token_handler(app: Flask) -> None:
    @app.route("/items", methods=["GET"])
    def items() -> Any:
        value = request.args.get("token", "")
        try:
            uuid.UUID(value)
        except (ValueError, AttributeError):
            return jsonify({"detail": _TOKEN_CONVERSION_ERROR}), 400
        return "", 200


def _token_query_param(*, example: object | None = None, examples: dict[str, dict] | None = None) -> dict:
    param: dict[str, Any] = {"name": "token", "in": "query", "required": True, "schema": {"type": "string"}}
    if example is not None:
        param["example"] = example
    if examples is not None:
        param["examples"] = examples
    return param


def token_with_example() -> OpenAPIApp:
    """GET /items with a single `NOT_A_UUID` example query parameter that fails server-side UUID parsing."""
    spec = build_schema(
        {
            "/items": {
                "get": {
                    "parameters": [_token_query_param(example="NOT_A_UUID")],
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Bad Request"}},
                }
            }
        }
    )
    app = make_flask_app_from_schema(spec)
    _register_token_handler(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def token_with_examples() -> OpenAPIApp:
    """GET /items with two named query examples, both rejected by the server's UUID parser."""
    spec = build_schema(
        {
            "/items": {
                "get": {
                    "parameters": [
                        _token_query_param(
                            examples={
                                "alpha": {"value": STALE_TOKENS[0]},
                                "beta": {"value": STALE_TOKENS[1]},
                            }
                        )
                    ],
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Bad Request"}},
                }
            }
        }
    )
    app = make_flask_app_from_schema(spec)
    _register_token_handler(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")
