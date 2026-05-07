from __future__ import annotations

from typing import Any

from flask import jsonify, request

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.runtime import OpenAPIApp

_BOUNDED_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("username", 3, 30),
    ("title", 5, 80),
    ("description", 10, 200),
)


def planted_bug() -> OpenAPIApp:
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
                    "400": {"description": "Bad Request"},
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
            return (
                jsonify(
                    {
                        "status_code": 400,
                        "detail": "Validation failed for POST /users",
                        "extra": [{"message": "Expected `object`, got `null`", "key": "data", "source": "body"}],
                    }
                ),
                400,
            )
        issues: list[dict[str, str]] = []
        for field, lo, hi in _BOUNDED_FIELDS:
            value = body.get(field, "")
            if not isinstance(value, str):
                value = ""
            if len(value) < lo:
                issues.append({"message": f"Expected `str` of length >= {lo}", "key": field, "source": "body"})
            elif len(value) > hi:
                issues.append({"message": f"Expected `str` of length <= {hi}", "key": field, "source": "body"})
        if issues:
            return (
                jsonify({"status_code": 400, "detail": "Validation failed for POST /users", "extra": issues}),
                400,
            )
        return "", 500

    return OpenAPIApp(spec=spec, server=app, kind="flask")
