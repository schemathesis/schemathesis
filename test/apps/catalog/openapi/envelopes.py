from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, NamedTuple

from flask import jsonify, request

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.runtime import OpenAPIApp

RailsEnvelope = Literal["modern", "legacy", "wrapped"]

_BOUNDED_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("username", 3, 30),
    ("title", 5, 80),
    ("description", 10, 200),
)

_STATUS_DESCRIPTIONS = {400: "Bad Request", 422: "Unprocessable Entity"}

_SYMFONY_LENGTH_MIN_CODE = "9ff3fdc4-b214-49db-8718-39c315e33d45"
_SYMFONY_LENGTH_MAX_CODE = "d94b19cc-114f-4f44-9cc4-4138e80a87b9"


class Violation(NamedTuple):
    field: str
    value: str
    minimum: int
    maximum: int
    too_short: bool

    @property
    def limit(self) -> int:
        return self.minimum if self.too_short else self.maximum


class Envelope(NamedTuple):
    status: int
    not_an_object: object
    render: Callable[[list[Violation]], object]


def planted_bug(envelope: Envelope) -> OpenAPIApp:
    # A 500 hides behind length-bounded fields that only the framework's validation errors reveal.
    paths = {
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
                                "required": [field for field, _, _ in _BOUNDED_FIELDS],
                            }
                        }
                    },
                },
                "responses": {
                    str(envelope.status): {"description": _STATUS_DESCRIPTIONS[envelope.status]},
                    "500": {"description": "Server Error"},
                },
            }
        }
    }
    spec = build_schema(paths)
    app = make_flask_app_from_schema(spec)

    @app.route("/users", methods=["POST"])
    def create_user() -> Any:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(envelope.not_an_object), envelope.status
        violations: list[Violation] = []
        for field, minimum, maximum in _BOUNDED_FIELDS:
            value = body.get(field, "")
            if not isinstance(value, str):
                value = ""
            too_short = len(value) < minimum
            if too_short or len(value) > maximum:
                violations.append(Violation(field, value, minimum, maximum, too_short))
        if violations:
            return jsonify(envelope.render(violations)), envelope.status
        return "", 500

    return OpenAPIApp(spec=spec, server=app, kind="flask")


def _render_ajv(violations: list[Violation]) -> object:
    # Structured `keyword` + `params.limit` carry the size bounds directly.
    return {
        "errors": [
            {
                "instancePath": f"/{violation.field}",
                "schemaPath": f"#/properties/{violation.field}/{'minLength' if violation.too_short else 'maxLength'}",
                "keyword": "minLength" if violation.too_short else "maxLength",
                "params": {"limit": violation.limit},
                "message": f"must NOT have {'fewer' if violation.too_short else 'more'} than {violation.limit} characters",
            }
            for violation in violations
        ]
    }


def _problem_details(errors: dict[str, list[str]]) -> dict[str, object]:
    return {
        "type": "https://tools.ietf.org/html/rfc9110#section-15.5.1",
        "title": "One or more validation errors occurred.",
        "status": 400,
        "errors": errors,
    }


def _render_aspnet(violations: list[Violation]) -> object:
    # ProblemDetails with the DataAnnotations `minimum length of 'N'` phrasing, keyed by C# property names.
    errors: dict[str, list[str]] = {}
    for violation in violations:
        name = violation.field.capitalize()
        bound = "minimum" if violation.too_short else "maximum"
        errors.setdefault(name, []).append(
            f"The field {name} must be a string or array type with a {bound} length of '{violation.limit}'."
        )
    return _problem_details(errors)


def _render_flask_rest(violations: list[Violation]) -> object:
    issues = {
        violation.field: f"'{violation.value}' is {'shorter' if violation.too_short else 'longer'} "
        f"than {violation.limit} characters"
        for violation in violations
    }
    return {"errors": issues, "message": "Input payload validation failed"}


def _render_go_validator(violations: list[Violation]) -> object:
    # Structured `tag` + `param` + `kind` carry the size bounds directly, keyed by Go struct field names.
    return {
        "errors": [
            {
                "field": violation.field.capitalize(),
                "kind": "string",
                "namespace": f"Body.{violation.field.capitalize()}",
                "param": str(violation.limit),
                "tag": "min" if violation.too_short else "max",
                "type": "string",
                "value": violation.value,
            }
            for violation in violations
        ]
    }


def _render_laravel(violations: list[Violation]) -> object:
    errors: dict[str, list[str]] = {}
    for violation in violations:
        if violation.too_short:
            message = f"The {violation.field} field must be at least {violation.limit} characters."
        else:
            message = f"The {violation.field} field must not be greater than {violation.limit} characters."
        errors.setdefault(violation.field, []).append(message)
    return {"message": "The given data was invalid.", "errors": errors}


def _render_litestar(violations: list[Violation]) -> object:
    issues = [
        {
            "message": f"Expected `str` of length {'>=' if violation.too_short else '<='} {violation.limit}",
            "key": violation.field,
            "source": "body",
        }
        for violation in violations
    ]
    return {"status_code": 400, "detail": "Validation failed for POST /users", "extra": issues}


def _render_marshmallow(violations: list[Violation]) -> object:
    return {
        violation.field: [f"Length must be between {violation.minimum} and {violation.maximum}."]
        for violation in violations
    }


def _rails_messages(violations: list[Violation]) -> list[tuple[str, str]]:
    return [
        (
            violation.field,
            f"is too short (minimum is {violation.limit} characters)"
            if violation.too_short
            else f"is too long (maximum is {violation.limit} characters)",
        )
        for violation in violations
    ]


def _rails_grouped(messages: list[tuple[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for field, message in messages:
        grouped.setdefault(field, []).append(message)
    return grouped


def _rails_legacy(messages: list[tuple[str, str]]) -> dict[str, list[str]]:
    return {"errors": [f"{field.replace('_', ' ').capitalize()} {message}" for field, message in messages]}


_RAILS_NOT_AN_OBJECT = [("base", "must be a valid JSON object")]


def _render_symfony(violations: list[Violation]) -> object:
    # `Length` UUID codes plus `{{ limit }}` parameters carry the size bounds directly.
    return [
        {
            "propertyPath": violation.field,
            "message": f"This value is too short. It should have {violation.limit} characters or more."
            if violation.too_short
            else f"This value is too long. It should have {violation.limit} characters or less.",
            "code": _SYMFONY_LENGTH_MIN_CODE if violation.too_short else _SYMFONY_LENGTH_MAX_CODE,
            "parameters": {
                "{{ limit }}": str(violation.limit),
                "{{ min }}": str(violation.minimum),
                "{{ max }}": str(violation.maximum),
            },
        }
        for violation in violations
    ]


def _render_zod(violations: list[Violation]) -> object:
    issues = []
    for violation in violations:
        if violation.too_short:
            issue: dict[str, object] = {"code": "too_small", "minimum": violation.limit}
            message = f"String must contain at least {violation.limit} character(s)"
        else:
            issue = {"code": "too_big", "maximum": violation.limit}
            message = f"String must contain at most {violation.limit} character(s)"
        issues.append(
            {
                **issue,
                "type": "string",
                "inclusive": True,
                "exact": False,
                "message": message,
                "path": [violation.field],
            }
        )
    return {"errors": issues}


AJV = Envelope(400, {"errors": []}, _render_ajv)
ASPNET = Envelope(400, _problem_details({}), _render_aspnet)
FLASK_REST = Envelope(
    400, {"errors": {"_schema": "Invalid input"}, "message": "Input payload validation failed"}, _render_flask_rest
)
GO_VALIDATOR = Envelope(400, {"errors": []}, _render_go_validator)
LARAVEL = Envelope(422, {"message": "The given data was invalid.", "errors": {}}, _render_laravel)
LITESTAR = Envelope(
    400,
    {
        "status_code": 400,
        "detail": "Validation failed for POST /users",
        "extra": [{"message": "Expected `object`, got `null`", "key": "data", "source": "body"}],
    },
    _render_litestar,
)
MARSHMALLOW = Envelope(422, {"_schema": ["Invalid input type."]}, _render_marshmallow)
RAILS: dict[RailsEnvelope, Envelope] = {
    "modern": Envelope(
        422,
        _rails_grouped(_RAILS_NOT_AN_OBJECT),
        lambda violations: _rails_grouped(_rails_messages(violations)),
    ),
    "legacy": Envelope(
        422,
        _rails_legacy(_RAILS_NOT_AN_OBJECT),
        lambda violations: _rails_legacy(_rails_messages(violations)),
    ),
    "wrapped": Envelope(
        422,
        {"errors": _rails_grouped(_RAILS_NOT_AN_OBJECT)},
        lambda violations: {"errors": _rails_grouped(_rails_messages(violations))},
    ),
}
SYMFONY = Envelope(422, [], _render_symfony)
ZOD = Envelope(400, {"errors": []}, _render_zod)
