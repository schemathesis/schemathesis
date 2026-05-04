from __future__ import annotations

from typing import Any

from flask import jsonify, request

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.runtime import OpenAPIApp

_LENGTH_MIN_CODE = "9ff3fdc4-b214-49db-8718-39c315e33d45"
_LENGTH_MAX_CODE = "d94b19cc-114f-4f44-9cc4-4138e80a87b9"

_BOUNDED_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("username", 3, 30),
    ("title", 5, 80),
    ("description", 10, 200),
)


def planted_bug() -> OpenAPIApp:
    # 422 violations carry Symfony's `Length` UUID codes plus `{{ limit }}` parameters
    # so the parser reads the bounds straight off the structured envelope.
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
                    "422": {"description": "Unprocessable Entity"},
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
            return jsonify([]), 422
        violations: list[dict] = []
        for field, lo, hi in _BOUNDED_FIELDS:
            value = body.get(field, "")
            if not isinstance(value, str):
                value = ""
            if len(value) < lo:
                violations.append(
                    {
                        "propertyPath": field,
                        "message": f"This value is too short. It should have {lo} characters or more.",
                        "code": _LENGTH_MIN_CODE,
                        "parameters": {"{{ limit }}": str(lo), "{{ min }}": str(lo), "{{ max }}": str(hi)},
                    }
                )
            elif len(value) > hi:
                violations.append(
                    {
                        "propertyPath": field,
                        "message": f"This value is too long. It should have {hi} characters or less.",
                        "code": _LENGTH_MAX_CODE,
                        "parameters": {"{{ limit }}": str(hi), "{{ min }}": str(lo), "{{ max }}": str(hi)},
                    }
                )
        if violations:
            return jsonify(violations), 422
        return "", 500

    return OpenAPIApp(spec=spec, server=app, kind="flask")
