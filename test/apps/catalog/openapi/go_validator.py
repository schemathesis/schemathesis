from __future__ import annotations

from typing import Any

from flask import jsonify, request

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.runtime import OpenAPIApp

_BOUNDED_FIELDS: tuple[tuple[str, str, int, int], ...] = (
    ("username", "Username", 3, 30),
    ("title", "Title", 5, 80),
    ("description", "Description", 10, 200),
)


def planted_bug() -> OpenAPIApp:
    # 400 issues use go-playground/validator's structured form so the parser
    # reads `tag`+`param`+`kind` directly to learn the size bounds.
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
                                "required": [json_name for json_name, _, _, _ in _BOUNDED_FIELDS],
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
        for json_name, struct_name, lo, hi in _BOUNDED_FIELDS:
            value = body.get(json_name, "")
            if not isinstance(value, str):
                value = ""
            if len(value) < lo:
                issues.append(
                    {
                        "field": struct_name,
                        "kind": "string",
                        "namespace": f"Body.{struct_name}",
                        "param": str(lo),
                        "tag": "min",
                        "type": "string",
                        "value": value,
                    }
                )
            elif len(value) > hi:
                issues.append(
                    {
                        "field": struct_name,
                        "kind": "string",
                        "namespace": f"Body.{struct_name}",
                        "param": str(hi),
                        "tag": "max",
                        "type": "string",
                        "value": value,
                    }
                )
        if issues:
            return jsonify({"errors": issues}), 400
        return "", 500

    return OpenAPIApp(spec=spec, server=app, kind="flask")
