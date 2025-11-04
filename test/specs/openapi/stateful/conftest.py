from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Literal

import hypothesis
import pytest
from flask import Flask, abort, jsonify, request

import schemathesis
from schemathesis.config import SchemathesisConfig
from schemathesis.engine.context import EngineContext
from schemathesis.engine.phases import Phase, PhaseName, stateful
from schemathesis.generation.modes import GenerationMode


@dataclass
class AppConfig:
    use_after_free: bool = False
    ensure_resource_availability: bool = False
    merge_body: bool = True
    independent_500: bool = False
    failure_behind_failure: bool = False
    multiple_conformance_issues: bool = False
    unsatisfiable: bool = False
    custom_headers: dict | None = None
    multiple_source_links: bool = False
    auth_token: str | None = None
    ignored_auth: bool = False
    slowdown: float | int | None = None
    multiple_incoming_links_with_same_status: bool = False
    duplicate_operation_links: bool = False
    circular_links: bool = False
    invalid_parameter: bool = False
    list_users_as_root: bool = False
    no_reliable_transitions: bool = False
    # For non-JSON response test
    return_plain_text: Literal[False] | str | bytes = False
    # For missing body parameter test
    omit_required_field: bool = False


@pytest.fixture
def app_factory(ctx):
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
    get_collection_links = {
        "GetUser": {
            "operationId": "getUser",
            "parameters": {"userId": "$response.body#/users/0/id"},
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
    schema = ctx.openapi.build_schema(
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

    app = Flask(__name__)
    config = AppConfig()
    app.config["schema"] = schema

    next_user_id = 1
    last_modified = "2021-01-01T00:00:00Z"
    users = {0: {"id": 0, "name": "John Doe", "last_modified": last_modified}}

    @app.route("/openapi.json", methods=["GET"])
    def get_spec():
        return jsonify(schema)

    @app.route("/users/<int:user_id>", methods=["GET"])
    def get_user(user_id):
        if config.slowdown:
            time.sleep(config.slowdown)
        user = users.get(user_id)
        if user:
            if config.return_plain_text is not False:
                return config.return_plain_text, 200, {"Content-Type": "text/plain"}
            if config.omit_required_field:
                # Return response without required 'id' field
                return jsonify({"name": user["name"], "last_modified": user["last_modified"]})
            return jsonify(user)
        return jsonify({"error": "User not found"}), 404

    @app.route("/users", methods=["GET"])
    def list_users():
        return jsonify(users)

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

        nonlocal next_user_id
        new_user = {"id": next_user_id, "name": name, "last_modified": last_modified}
        if config.duplicate_operation_links:
            new_user["manager_id"] = 0
        if config.return_plain_text is not False:
            new_user["id"] = next_user_id = 192
        if not config.ensure_resource_availability:
            # Do not always save the user
            users[next_user_id] = new_user
        next_user_id += 1

        if config.omit_required_field:
            # Return response without required 'id' field
            return jsonify({"name": new_user["name"], "last_modified": new_user["last_modified"]}), 201
        return jsonify(new_user), 201

    @app.route("/users/<int:user_id>", methods=["PATCH"])
    def update_user(user_id):
        if config.slowdown:
            time.sleep(config.slowdown)
        user = users.get(user_id)
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
        user = users.get(user_id)
        if config.independent_500:
            return jsonify({"error": "Something went wrong - DELETE"}), 500
        if user:
            # Only delete users with short names
            if config.use_after_free:
                if len(user["name"]) < 10:
                    del users[user_id]
            else:
                del users[user_id]
            return jsonify({"message": "User deleted successfully"}), 204
        return jsonify({"error": "User not found"}), 404

    deleted_orders = set()

    @app.route("/orders/<order_id>", methods=["DELETE"])
    def delete_order(order_id):
        if config.slowdown:
            time.sleep(config.slowdown)
        if order_id in deleted_orders:
            return jsonify({"error": "Order not found"}), 404
        deleted_orders.add(order_id)
        return jsonify({"message": "Nothing happened"}), 200

    @app.before_request
    def check_auth():
        if config.ignored_auth or config.auth_token is None or request.endpoint == get_spec.__name__:
            # Allow all requests if auth is ignored or no token is set + to schema
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

    def _factory(
        *,
        use_after_free=False,
        ensure_resource_availability=False,
        merge_body=True,
        independent_500=False,
        failure_behind_failure=False,
        multiple_conformance_issues=False,
        unsatisfiable=False,
        custom_headers=None,
        multiple_source_links=False,
        single_link=False,
        auth_token=None,
        ignored_auth=False,
        slowdown=None,
        multiple_incoming_links_with_same_status=False,
        circular_links: bool = False,
        duplicate_operation_links: bool = False,
        invalid_parameter: bool = False,
        list_users_as_root: bool = False,
        no_reliable_transitions: bool = False,
        return_plain_text: Literal[False] | str | bytes = False,
        omit_required_field: bool = False,
    ):
        config.use_after_free = use_after_free
        config.ensure_resource_availability = ensure_resource_availability
        config.auth_token = auth_token
        config.ignored_auth = ignored_auth
        config.return_plain_text = return_plain_text
        if return_plain_text is not False or omit_required_field is not False:
            # To simplify snapshots
            schema["components"]["schemas"]["NewUser"]["properties"]["name"] = {"enum": ["fixed-name"]}
        if omit_required_field:
            link = post_links["DeleteUser"]
            post_links.clear()
            post_links["DeleteUser"] = link
            get_links.clear()
            delete_links.clear()
            order_links.clear()

        config.omit_required_field = omit_required_field
        if ignored_auth:
            schema["components"]["securitySchemes"] = {
                "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
            }
            schema["security"] = [{"bearerAuth": []}]

        config.merge_body = merge_body
        if not merge_body:
            schema["paths"]["/users"]["post"]["responses"]["201"]["links"]["UpdateUser"]["x-schemathesis"] = {
                "merge_body": merge_body
            }
        config.independent_500 = independent_500
        config.failure_behind_failure = failure_behind_failure
        config.multiple_conformance_issues = multiple_conformance_issues
        config.unsatisfiable = unsatisfiable
        if unsatisfiable:
            schema["components"]["schemas"]["NewUser"]["properties"]["name"]["minLength"] = 100
        if custom_headers:
            config.custom_headers = custom_headers
        config.multiple_source_links = multiple_source_links
        if multiple_source_links:
            schema["paths"]["/users/{userId}"]["delete"]["responses"]["204"]["links"]["DeleteUserAgain"] = {
                "operationId": "deleteUser",
                "parameters": {"userId": "$request.path.userId"},
            }
            link = post_links["DeleteUser"]
            post_links.clear()
            post_links["DeleteUser"] = link
            get_links.clear()
        if single_link:
            link = post_links["DeleteUser"]
            post_links.clear()
            post_links["DeleteUser"] = link
            get_links.clear()
            order_links.clear()
        if slowdown:
            config.slowdown = slowdown
        if multiple_incoming_links_with_same_status:
            schema["paths"]["/users/{userId}"]["patch"]["responses"]["200"]["links"] = {
                "GetUser": {
                    "operationId": "getUser",
                    "parameters": {"userId": "$request.path.userId"},
                }
            }
        if circular_links:
            # Add link from DELETE back to POST
            delete_links["CreateNewUser"] = {
                "operationId": "createUser",
                "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/NewUser"}}}},
            }
        if duplicate_operation_links:
            # Add manager ID to User schema
            schema["components"]["schemas"]["User"]["properties"]["manager_id"] = {"type": "integer"}
            # Add second link to the same operation
            post_links["GetManager"] = {
                "operationId": "getUser",
                "parameters": {"userId": "$response.body#/manager_id"},
                "description": "Get user's manager",
            }
        if invalid_parameter:
            # Add a link with reference to non-existent parameter
            for name in ("InvalidUser", "InvalidUser-2"):
                schema["paths"]["/users"]["post"]["responses"]["201"]["links"][name] = {
                    "operationId": "getUser",
                    "parameters": {
                        "unknown": "$response.body#/id",  # `unknown` parameter doesn't exist in GET /users/{userId}
                        "userId": "$request.query.wrong",  # `wrong` parameter doesn't exist in POST /users
                    },
                }
            schema["paths"]["/users/{userId}"]["patch"]["responses"]["200"]["links"] = {
                "GetUser": {
                    "operationId": "getUser",
                    "parameters": {
                        "userId": "$request.path.whatever",
                        "something": "$req.[",
                    },
                }
            }
        if list_users_as_root:
            schema["paths"]["/users"]["get"]["responses"]["200"]["links"] = get_collection_links
            post_links.clear()
        if no_reliable_transitions:
            # Remove POST endpoint completely
            del schema["paths"]["/users"]["post"]
        return app

    return _factory


@pytest.fixture
def stop_event():
    return threading.Event()


@pytest.fixture
def engine_factory(app_factory, app_runner, stop_event):
    def _engine_factory(
        *,
        app_kwargs=None,
        hypothesis_settings=None,
        max_examples=None,
        max_steps=None,
        maximize=None,
        checks=None,
        max_failures=None,
        unique_inputs=False,
        generation_modes=None,
        include=None,
        headers=None,
        max_response_time=None,
    ):
        app = app_factory(**(app_kwargs or {}))
        port = app_runner.run_flask_app(app)
        config = SchemathesisConfig()
        config.update(max_failures=max_failures)
        config.projects.override.checks.update(
            included_check_names=[func.__name__ for func in checks] if isinstance(checks, list) else None,
            max_response_time=max_response_time,
        )
        if max_steps is not None:
            config.projects.override.phases.stateful.max_steps = max_steps
        config.projects.override.phases.stateful.inference.algorithms = []
        config.projects.override.generation.update(
            modes=generation_modes or [GenerationMode.POSITIVE],
            unique_inputs=unique_inputs,
            max_examples=max_examples,
            maximize=maximize,
        )
        config.projects.override.update(headers=headers)
        schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json", config=config)

        if hypothesis_settings is not None:
            current = schema.config.get_hypothesis_settings()
            new = hypothesis.settings(current, **hypothesis_settings)
            schema.config.get_hypothesis_settings = lambda *_, **__: new

        if include is not None:
            schema = schema.include(**include)
        return stateful.execute(
            engine=EngineContext(schema=schema, stop_event=stop_event),
            phase=Phase(name=PhaseName.STATEFUL_TESTING, is_supported=True, is_enabled=True),
        )

    return _engine_factory
