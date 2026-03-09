from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from flask import Flask, abort, jsonify, request

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.runtime import Modifier, OpenAPIApp


@dataclass
class StatefulConfig:
    use_after_free: bool = False
    ensure_resource_availability: bool = False
    merge_body: bool = True
    independent_500: bool = False
    failure_behind_failure: bool = False
    multiple_conformance_issues: bool = False
    custom_headers: dict | None = None
    auth_token: str | None = None
    enforce_auth: bool = True
    slowdown: float | int | None = None
    duplicate_operation_links: bool = False
    return_plain_text: Literal[False] | str | bytes = False
    omit_required_field: bool = False
    reuse_deleted_ids: bool = False
    # Link-calibration attribution scenarios; each flag forces a specific 4xx shape on user-detail routes.
    parser_blames_unrelated: bool = False
    wrong_link_parser_attributed: bool = False
    wrong_link_type_mismatch: bool = False
    wrong_link_to_missing_id: bool = False


@dataclass
class UserStore:
    config: StatefulConfig = field(default_factory=StatefulConfig)
    users: dict[int, dict] = field(
        default_factory=lambda: {0: {"id": 0, "name": "John Doe", "last_modified": "2021-01-01T00:00:00Z"}}
    )
    next_user_id: int = 1
    freed_ids: list[int] = field(default_factory=list)
    deleted_orders: set = field(default_factory=set)


def stateful_users(*modifiers: Modifier[UserStore]) -> OpenAPIApp:
    spec = _build_stateful_schema()
    app = make_flask_app_from_schema(spec)
    app.config["schema"] = spec
    store = UserStore()
    _register_handlers(app, store)
    for modifier in sorted(modifiers, key=lambda m: m.priority):
        modifier.apply(app, store)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def _build_stateful_schema() -> dict:
    post_links = {
        "GetUser": {
            "operationId": "getUser",
            "parameters": {"userId": "$response.body#/id"},
        },
        "DeleteUser": {
            "operationId": "deleteUser",
            "parameters": {"userId": "$response.body#/id"},
        },
        "UpdateUser": {
            "operationId": "updateUser",
            "parameters": {"userId": "$response.body#/id"},
            "requestBody": {
                "last_modified": "$response.body#/last_modified",
            },
        },
        "DeleteOrder": {"operationId": "deleteOrder", "parameters": {"orderId": 42}},
    }
    get_links = {
        "DeleteUser": {
            "operationId": "deleteUser",
            "parameters": {"userId": "$response.body#/id"},
        },
    }
    delete_links = {
        "GetUser": {
            "operationId": "getUser",
            "parameters": {"userId": "$request.path.userId"},
        },
    }
    order_links = {
        "GetUser": {
            "operationId": "getUser",
            "parameters": {"userId": 42},
        },
    }
    return build_schema(
        {
            "/users": {
                "get": {
                    "operationId": "getUsers",
                    "responses": {
                        "200": {
                            "description": "List of users",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "users": {"type": "array", "items": {"$ref": "#/components/schemas/User"}}
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
                "post": {
                    "operationId": "createUser",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/NewUser"}}},
                    },
                    "responses": {
                        "201": {
                            "description": "Successful response",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/User"}}},
                            "links": post_links,
                        },
                        "400": {"description": "Bad request"},
                        "default": {"description": "Default"},
                    },
                },
            },
            "/users/{userId}": {
                "parameters": [{"in": "path", "name": "userId", "required": True, "schema": {"type": "integer"}}],
                "get": {
                    "summary": "Get a user",
                    "operationId": "getUser",
                    "responses": {
                        "200": {
                            "description": "Successful response",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/User"}}},
                            "links": get_links,
                        },
                        "404": {"description": "User not found"},
                        "default": {"description": "Default"},
                    },
                },
                "delete": {
                    "summary": "Delete a user",
                    "operationId": "deleteUser",
                    "responses": {
                        "204": {
                            "description": "Successful response",
                            "links": delete_links,
                        },
                        "404": {"description": "User not found"},
                        "default": {"description": "Default"},
                    },
                },
                "patch": {
                    "summary": "Update a user",
                    "operationId": "updateUser",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/UpdateUser"}}},
                    },
                    "responses": {
                        "200": {
                            "description": "Successful response",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/User"}}},
                        },
                        "404": {"description": "User not found"},
                        "default": {"description": "Default"},
                    },
                },
            },
            "/orders/{orderId}": {
                # Stub to check that not every "DELETE" counts
                "delete": {
                    "parameters": [{"in": "path", "name": "orderId", "required": True, "schema": {"type": "integer"}}],
                    "operationId": "deleteOrder",
                    "responses": {
                        "200": {
                            "description": "Successful response",
                            "links": order_links,
                        },
                        "default": {"description": "Default"},
                    },
                },
            },
        },
        components={
            "schemas": {
                "User": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "last_modified": {"type": "string"},
                    },
                    "required": ["id", "name", "last_modified"],
                },
                "NewUser": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string", "maxLength": 50}},
                    "additionalProperties": False,
                },
                "UpdateUser": {
                    "type": "object",
                    "required": ["name", "last_modified"],
                    "properties": {
                        "name": {"type": "string"},
                        "last_modified": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            }
        },
    )


def _register_handlers(app: Flask, store: UserStore) -> None:
    last_modified = "2021-01-01T00:00:00Z"
    config = store.config

    # Counts user-detail hits so tests can prove the link target was actually exercised.
    target_request_count = {"count": 0}
    app.config["target_request_count"] = target_request_count

    @app.before_request
    def _count_user_detail_requests() -> None:
        rule = request.url_rule
        if rule is not None and rule.rule.startswith("/users/<"):
            target_request_count["count"] += 1

    @app.route("/users/<int:user_id>", methods=["GET"])
    def get_user(user_id):
        if config.parser_blames_unrelated:
            return jsonify({"errors": [{"field": "X-Tenant-Id", "defaultMessage": "must not be blank"}]}), 400
        if config.wrong_link_parser_attributed:
            return jsonify({"userId": ["This field must be a UUID."]}), 422
        if config.slowdown:
            time.sleep(config.slowdown)
        user = store.users.get(user_id)
        if user:
            if config.return_plain_text is not False:
                return config.return_plain_text, 200, {"Content-Type": "text/plain"}
            if config.omit_required_field:
                return jsonify({"name": user["name"], "last_modified": user["last_modified"]})
            return jsonify(user)
        return jsonify({"error": "User not found"}), 404

    @app.route("/users/<user_id>", methods=["GET", "PATCH", "DELETE"])
    def get_user_string(user_id):
        # Fallback for non-integer userId values that wrong-link rewrites push into the URL.
        if config.wrong_link_parser_attributed:
            return jsonify({"userId": ["This field must be a UUID."]}), 422
        if config.wrong_link_type_mismatch:
            return jsonify({"error": "userId must be integer"}), 400
        return jsonify({"error": "User not found"}), 404

    @app.route("/users", methods=["GET"])
    def list_users():
        return jsonify(store.users)

    def expect_custom_headers():
        if config.custom_headers:
            for key, value in config.custom_headers.items():
                assert request.headers.get(key) == value

    @app.route("/users", methods=["POST"])
    def create_user():
        if config.slowdown:
            time.sleep(config.slowdown)
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid input"}), 400
        name = data.get("name")
        expect_custom_headers()
        if name is None:
            return jsonify({"error": "Name is required"}), 400

        if config.multiple_conformance_issues:
            response = jsonify({"error": "Error - multiple conformance issues"})
            del response.headers["Content-Type"]
            return response, 201

        if config.failure_behind_failure:
            if len(name) % 10 < 7:
                return jsonify({"invalid": "user structure"}), 201
            return jsonify({"error": "Error - rare"}), 500

        if config.reuse_deleted_ids and store.freed_ids:
            new_id = store.freed_ids.pop(0)
        else:
            new_id = store.next_user_id
            store.next_user_id += 1
        new_user = {"id": new_id, "name": name, "last_modified": last_modified}
        if config.duplicate_operation_links:
            new_user["manager_id"] = 0
        if config.wrong_link_to_missing_id:
            # Integer that never resolves to a user; the wrong-link rewrite feeds it into DELETE's path.
            new_user["manager_id"] = 99999
        if config.return_plain_text is not False:
            new_user["id"] = new_id = 192
            store.next_user_id = 192
        if not config.ensure_resource_availability:
            store.users[new_id] = new_user

        if config.omit_required_field:
            return jsonify({"name": new_user["name"], "last_modified": new_user["last_modified"]}), 201
        return jsonify(new_user), 201

    @app.route("/users/<int:user_id>", methods=["PATCH"])
    def update_user(user_id):
        if config.slowdown:
            time.sleep(config.slowdown)
        user = store.users.get(user_id)
        if config.independent_500:
            return jsonify({"error": "Something went wrong - PATCH"}), 500
        if user:
            data = request.get_json()
            if config.merge_body:
                assert "name" in data
                user["name"] = data["name"]
            return jsonify(user)
        return jsonify({"error": "User not found"}), 404

    @app.route("/users/<int:user_id>", methods=["DELETE"])
    def delete_user(user_id):
        if config.slowdown:
            time.sleep(config.slowdown)
        user = store.users.get(user_id)
        if config.independent_500:
            return jsonify({"error": "Something went wrong - DELETE"}), 500
        if user:
            # Only delete users with short names when use_after_free is on
            if config.use_after_free:
                if len(user["name"]) < 10:
                    del store.users[user_id]
            else:
                del store.users[user_id]
            if config.reuse_deleted_ids:
                store.freed_ids.append(user_id)
            return jsonify({"message": "User deleted successfully"}), 204
        return jsonify({"error": "User not found"}), 404

    @app.route("/orders/<order_id>", methods=["DELETE"])
    def delete_order(order_id):
        if config.slowdown:
            time.sleep(config.slowdown)
        if order_id in store.deleted_orders:
            return jsonify({"error": "Order not found"}), 404
        store.deleted_orders.add(order_id)
        return jsonify({"message": "Nothing happened"}), 200

    @app.before_request
    def check_auth():
        if not config.enforce_auth or config.auth_token is None or request.endpoint == "openapi_spec":
            return

        auth_header = request.headers.get("Authorization")
        if not auth_header:
            abort(401, description="Authorization header is missing")

        try:
            token_type, token = auth_header.split()
            if token_type.lower() != "bearer":
                abort(401, description="Invalid token type")

            if token != config.auth_token:
                abort(401, description="Invalid token")
        except ValueError:
            abort(401, description="Invalid Authorization header format")
