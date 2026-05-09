from __future__ import annotations

from typing import Any

from flask import Flask, jsonify, request

# OpenAPI paths fed to ctx.openapi.build_schema.
PATHS: dict[str, Any] = {
    "/always-200": {"get": {"responses": {"200": {"description": "OK"}}}},
    "/always-500": {"get": {"responses": {"200": {"description": "OK"}}}},
    "/echo-validate": {
        "post": {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["name"],
                            "properties": {"name": {"type": "string", "minLength": 1}},
                            "additionalProperties": False,
                        }
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        }
    },
    "/auth": {"get": {"responses": {"200": {"description": "OK"}}}},
    "/items/{id}": {
        "get": {
            "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
            "responses": {"200": {"description": "OK"}},
        }
    },
    "/redirect": {"get": {"responses": {"200": {"description": "OK"}}}},
}


def attach_handlers(app: Flask) -> None:
    @app.route("/always-200")
    def always_200():
        return jsonify({}), 200

    @app.route("/always-500")
    def always_500():
        return jsonify({"error": "boom"}), 500

    @app.route("/echo-validate", methods=["POST"])
    def echo_validate():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not data["name"]:
            return jsonify({"detail": "invalid"}), 422
        return jsonify(data), 200

    @app.route("/auth")
    def auth():
        return jsonify({"detail": "unauthorized"}), 401

    @app.route("/items/<int:id>")
    def items(id: int):  # noqa: A002
        if id in {1, 2, 3}:
            return jsonify({"id": id}), 200
        return jsonify({"detail": "not found"}), 404

    @app.route("/redirect")
    def redirect_route():
        return ("", 302, {"Location": "/always-200"})
