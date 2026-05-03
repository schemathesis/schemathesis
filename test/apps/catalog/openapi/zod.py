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
    # 400 issues use Zod's structured `too_small` / `type=string` envelope so the
    # error-feedback parser can learn the length bounds that gate the 500.
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
            return jsonify({"errors": []}), 400
        issues: list[dict] = []
        for field, lo, hi in _BOUNDED_FIELDS:
            value = body.get(field, "")
            if not isinstance(value, str):
                value = ""
            if len(value) < lo:
                issues.append(
                    {
                        "code": "too_small",
                        "minimum": lo,
                        "type": "string",
                        "inclusive": True,
                        "exact": False,
                        "message": f"String must contain at least {lo} character(s)",
                        "path": [field],
                    }
                )
            elif len(value) > hi:
                issues.append(
                    {
                        "code": "too_big",
                        "maximum": hi,
                        "type": "string",
                        "inclusive": True,
                        "exact": False,
                        "message": f"String must contain at most {hi} character(s)",
                        "path": [field],
                    }
                )
        if issues:
            return jsonify({"errors": issues}), 400
        return "", 500

    return OpenAPIApp(spec=spec, server=app, kind="flask")
