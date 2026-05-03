from __future__ import annotations

from flask import Flask, jsonify, request

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.runtime import OpenAPIApp

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
