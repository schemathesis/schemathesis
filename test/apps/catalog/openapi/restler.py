from __future__ import annotations

from typing import Any

from flask import jsonify, request

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.runtime import OpenAPIApp


def planted_bug() -> OpenAPIApp:
    # Restler-generated specs can wrap the body in `request_data` while the server reads fields from the JSON root.
    paths = {
        "/api/tickets": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"request_data": {"type": "array", "items": {"type": "string"}}},
                            }
                        }
                    },
                },
                "responses": {
                    "200": {"description": "OK"},
                    "400": {"description": "Bad Request"},
                    "500": {"description": "Server Error"},
                },
            }
        }
    }
    spec = build_schema(paths)
    app = make_flask_app_from_schema(spec)

    @app.route("/api/tickets", methods=["POST"])
    def create_ticket() -> Any:
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.get("subject") is None:
            return jsonify({"error": {"code": 400, "message": "Bad Request: subject field missing"}}), 400
        return "", 500

    return OpenAPIApp(spec=spec, server=app, kind="flask")
