from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from flask import jsonify, request

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.runtime import OpenAPIApp

Envelope = Literal["modern", "legacy", "wrapped"]

_BOUNDED_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("username", 3, 30),
    ("title", 5, 80),
    ("description", 10, 200),
)


def _modern_envelope(issues: list[tuple[str, str]]) -> tuple[dict[str, Any], int]:
    body: dict[str, list[str]] = {}
    for field, message in issues:
        body.setdefault(field, []).append(message)
    return body, 422


def _legacy_envelope(issues: list[tuple[str, str]]) -> tuple[dict[str, Any], int]:
    return {"errors": [f"{field.replace('_', ' ').capitalize()} {msg}" for field, msg in issues]}, 422


def _wrapped_envelope(issues: list[tuple[str, str]]) -> tuple[dict[str, Any], int]:
    body: dict[str, list[str]] = {}
    for field, message in issues:
        body.setdefault(field, []).append(message)
    return {"errors": body}, 422


_ENVELOPES: dict[str, Callable[[list[tuple[str, str]]], tuple[dict[str, Any], int]]] = {
    "modern": _modern_envelope,
    "legacy": _legacy_envelope,
    "wrapped": _wrapped_envelope,
}


def planted_bug(envelope: Envelope) -> OpenAPIApp:
    # 422 messages use the Rails "is too short (minimum is N characters)" phrasing
    # so the error-feedback parser can learn the length bounds that gate the 500.
    emit = _ENVELOPES[envelope]
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
            payload, status = emit([("base", "must be a valid JSON object")])
            return jsonify(payload), status
        issues: list[tuple[str, str]] = []
        for field, lo, hi in _BOUNDED_FIELDS:
            value = body.get(field, "")
            if not isinstance(value, str):
                value = ""
            if len(value) < lo:
                issues.append((field, f"is too short (minimum is {lo} characters)"))
            elif len(value) > hi:
                issues.append((field, f"is too long (maximum is {hi} characters)"))
        if issues:
            payload, status = emit(issues)
            return jsonify(payload), status
        return "", 500

    return OpenAPIApp(spec=spec, server=app, kind="flask")
