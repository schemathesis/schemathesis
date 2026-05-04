from __future__ import annotations

from dataclasses import dataclass, field

from flask import jsonify

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.runtime import OpenAPIApp


@dataclass
class UnimplementedMethodStore:
    """Wire counters surfaced via `app.config['store']` for assertions."""

    hits: dict[str, int] = field(default_factory=lambda: {"missing": 0, "items": 0, "items_id": 0})


# Body has six fields so the Coverage phase generates enough positive-mode mutations
# to drive the supervisor's streak past `METHOD_NOT_ALLOWED_THRESHOLD` before later
# phases queue scenarios.
_MISSING_BODY_PROPERTIES: dict[str, dict[str, str]] = {
    "name": {"type": "string"},
    "size": {"type": "integer"},
    "kind": {"type": "string"},
    "tag": {"type": "string"},
    "count": {"type": "integer"},
    "rank": {"type": "integer"},
}


def unimplemented_method() -> OpenAPIApp:
    """Two-operation app: a healthy `GET /items` and a `POST /missing` that always returns 405."""
    spec = build_schema(
        {
            "/missing": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"type": "object", "properties": _MISSING_BODY_PROPERTIES}}
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/items": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )
    app = make_flask_app_from_schema(spec)
    store = UnimplementedMethodStore()
    app.config["store"] = store

    @app.route("/missing", methods=["POST"])
    def missing_post():
        store.hits["missing"] += 1
        return jsonify({"error": "Method Not Allowed"}), 405

    @app.route("/items", methods=["GET"])
    def items_get():
        store.hits["items"] += 1
        return jsonify({"ok": True}), 200

    return OpenAPIApp(spec=spec, server=app, kind="flask")


def linked_with_unimplemented_method() -> OpenAPIApp:
    """`GET /items` -> `GET /items/{itemId}` link plus a `POST /missing` that always returns 405.

    The link gives the stateful state machine a meaningful chain so it actually runs;
    the dead `POST /missing` is the operation the supervisor must filter out.
    """
    spec = build_schema(
        {
            "/items": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {"id": {"type": "integer"}},
                                            "required": ["id"],
                                        },
                                    }
                                }
                            },
                            "links": {
                                "GetItem": {
                                    "operationId": "getItem",
                                    "parameters": {"itemId": "$response.body#/0/id"},
                                }
                            },
                        }
                    }
                }
            },
            "/items/{itemId}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/missing": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"type": "object", "properties": _MISSING_BODY_PROPERTIES}}
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    app = make_flask_app_from_schema(spec)
    store = UnimplementedMethodStore()
    app.config["store"] = store

    @app.route("/items", methods=["GET"])
    def items_get():
        store.hits["items"] += 1
        return jsonify([{"id": 1}, {"id": 2}, {"id": 3}]), 200

    @app.route("/items/<int:item_id>", methods=["GET"])
    def items_id(item_id: int):
        store.hits["items_id"] += 1
        return jsonify({"id": item_id}), 200

    @app.route("/missing", methods=["POST"])
    def missing_post():
        store.hits["missing"] += 1
        return jsonify({"error": "Method Not Allowed"}), 405

    return OpenAPIApp(spec=spec, server=app, kind="flask")
