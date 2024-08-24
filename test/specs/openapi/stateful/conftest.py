from __future__ import annotations

import time
from dataclasses import dataclass

import hypothesis
import pytest
from flask import Flask, jsonify, request

import schemathesis
from schemathesis.stateful.runner import StatefulTestRunnerConfig


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
    slowdown: float | int | None = None


@pytest.fixture
def app_factory(empty_open_api_3_schema):
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
            "parameters": {"userId": "$request.path.userId"},
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
    empty_open_api_3_schema["paths"] = {
        "/users": {
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
    }
    empty_open_api_3_schema["components"] = {
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
    }

    app = Flask(__name__)
    config = AppConfig()

    users = {}
    next_user_id = 1
    last_modified = "2021-01-01T00:00:00Z"

    @app.route("/openapi.json", methods=["GET"])
    def get_spec():
        return jsonify(empty_open_api_3_schema)

    @app.route("/users/<int:user_id>", methods=["GET"])
    def get_user(user_id):
        if config.slowdown:
            time.sleep(config.slowdown)
        user = users.get(user_id)
        if user:
            return jsonify(user)
        else:
            return jsonify({"error": "User not found"}), 404

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
            if len(name) % 10 == 7:
                return jsonify({"invalid": "user structure"}), 201
            else:
                return jsonify({"error": "Error - rare"}), 500

        nonlocal next_user_id
        new_user = {"id": next_user_id, "name": name, "last_modified": last_modified}
        if not config.ensure_resource_availability:
            # Do not always save the user
            users[next_user_id] = new_user
        next_user_id += 1

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
            assert data["last_modified"] == user["last_modified"]
            if not config.merge_body:
                assert len(data) == 1
            else:
                assert "name" in data
                user["name"] = data["name"]
            return jsonify(user)
        else:
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
        else:
            return jsonify({"error": "User not found"}), 404

    @app.route("/orders/<order_id>", methods=["DELETE"])
    def delete_order(order_id):
        if config.slowdown:
            time.sleep(config.slowdown)
        return jsonify({"message": "Nothing happened"}), 200

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
        slowdown=None,
    ):
        config.use_after_free = use_after_free
        config.ensure_resource_availability = ensure_resource_availability
        config.merge_body = merge_body
        if not merge_body:
            empty_open_api_3_schema["paths"]["/users"]["post"]["responses"]["201"]["links"]["UpdateUser"][
                "x-schemathesis"
            ] = {"merge_body": merge_body}
        config.independent_500 = independent_500
        config.failure_behind_failure = failure_behind_failure
        config.multiple_conformance_issues = multiple_conformance_issues
        config.unsatisfiable = unsatisfiable
        if unsatisfiable:
            empty_open_api_3_schema["components"]["schemas"]["NewUser"]["properties"]["name"]["minLength"] = 100
        if custom_headers:
            config.custom_headers = custom_headers
        config.multiple_source_links = multiple_source_links
        if multiple_source_links:
            empty_open_api_3_schema["paths"]["/users/{userId}"]["delete"]["responses"]["204"]["links"][
                "DeleteUserAgain"
            ] = {
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
        return app

    return _factory


@pytest.fixture
def runner_factory(app_factory):
    def _runner_factory(*, app_kwargs=None, config_kwargs=None, loader_kwargs=None):
        app = app_factory(**(app_kwargs or {}))
        schema = schemathesis.from_wsgi("/openapi.json", app=app, **(loader_kwargs or {}))
        state_machine = schema.as_state_machine()
        config_kwargs = config_kwargs or {}
        config_kwargs.setdefault("hypothesis_settings", hypothesis.settings(max_examples=55, database=None))
        return state_machine.runner(config=StatefulTestRunnerConfig(**config_kwargs))

    return _runner_factory
