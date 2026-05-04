from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from flask import Flask, jsonify, request
from pydantic import BaseModel, ConfigDict, Field

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.fragments import handlers, schemas
from test.apps.runtime import OpenAPIApp


def _build_users_schema() -> dict:
    return build_schema(
        {
            "/users/": {
                "post": {
                    "operationId": "createUser",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "first_name": {"type": "string", "minLength": 3},
                                        "last_name": {"type": "string", "minLength": 3},
                                    },
                                    "required": ["first_name", "last_name"],
                                    "additionalProperties": False,
                                },
                                "example": {"first_name": "John", "last_name": "Doe"},
                            }
                        },
                        "required": True,
                    },
                    "responses": {"201": {"$ref": "#/components/responses/ResponseWithLinks"}},
                }
            },
            "/users/{user_id}": {
                "parameters": [
                    {"in": "path", "name": "user_id", "required": True, "schema": {"type": "string"}},
                    {"in": "query", "name": "common", "required": True, "schema": {"type": "integer"}},
                ],
                "get": {
                    "operationId": "getUser",
                    "parameters": [
                        {"in": "query", "name": "code", "required": True, "schema": {"type": "integer"}},
                        {
                            "in": "query",
                            "name": "user_id",
                            "required": True,
                            "schema": {"type": "string"},
                            "example": "test-id",
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "links": {
                                "UpdateUserById": {
                                    "operationRef": "#/paths/~1users~1{user_id}/patch",
                                    "parameters": {"user_id": "$response.body#/id"},
                                    "requestBody": {"first_name": "foo", "last_name": "bar"},
                                }
                            },
                        },
                        "404": {"description": "Not found"},
                    },
                },
                "patch": {
                    "operationId": "updateUser",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "first_name": {"type": "string", "minLength": 3},
                                        # Planted bug: `last_name` is incorrectly nullable, so the GET
                                        # handler's `first + " " + last` blows up on a follow-up read.
                                        "last_name": {"type": "string", "minLength": 3, "nullable": True},
                                    },
                                    "required": ["first_name", "last_name"],
                                    "additionalProperties": False,
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {
                        "200": {
                            "description": "OK",
                            "links": {
                                "GetUserById": {
                                    "operationId": "getUser",
                                    "parameters": {"path.user_id": "$request.path.user_id"},
                                }
                            },
                        },
                        "404": {"description": "Not found"},
                    },
                },
            },
        },
        components={
            "links": {
                "UpdateUserById": {
                    "operationId": "updateUser",
                    "parameters": {"user_id": "$response.body#/id"},
                },
            },
            "responses": {
                "ResponseWithLinks": {
                    "description": "OK",
                    "links": {
                        "GetUserByUserId": {
                            "operationId": "getUser",
                            "parameters": {
                                "path.user_id": "$response.body#/id",
                                "query.user_id": "$response.body#/id",
                            },
                        },
                        "UpdateUserById": {"$ref": "#/components/links/UpdateUserById"},
                    },
                }
            },
        },
    )


def _register_users_handlers(app: Flask) -> None:
    app.config.setdefault("users", {})

    @app.route("/users/", methods=["POST"])
    def create_user() -> Any:
        data = request.json
        if not isinstance(data, dict):
            return jsonify({"detail": "Invalid payload"}), 400
        for field in ("first_name", "last_name"):
            if field not in data:
                return jsonify({"detail": f"Missing `{field}`"}), 400
            if not isinstance(data[field], str):
                return jsonify({"detail": f"Invalid `{field}`"}), 400
        user_id = str(uuid4())
        app.config["users"][user_id] = {**data, "id": user_id}
        return jsonify({"id": user_id}), 201

    @app.route("/users/<user_id>", methods=["GET"])
    def get_user(user_id: str) -> Any:
        try:
            user = app.config["users"][user_id]
            # Concatenation surfaces the planted bug when `last_name` is `None`.
            full_name = user["first_name"] + " " + user["last_name"]
            return jsonify({"id": user["id"], "full_name": full_name})
        except KeyError:
            return jsonify({"message": "Not found"}), 404

    @app.route("/users/<user_id>", methods=["PATCH"])
    def update_user(user_id: str) -> Any:
        try:
            user = app.config["users"][user_id]
            data = request.json
            for field in ("first_name", "last_name"):
                if field not in data:
                    return jsonify({"detail": f"Missing `{field}`"}), 400
                # No type check on purpose — emulates a buggy operation that accepts `None`.
                user[field] = data[field]
            return jsonify(user)
        except KeyError:
            return jsonify({"message": "Not found"}), 404


def crud() -> OpenAPIApp:
    spec = _build_users_schema()
    app = make_flask_app_from_schema(spec)
    _register_users_handlers(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def create_user_only() -> OpenAPIApp:
    """Just POST /users/ — used to verify behaviour when stateful linking has no target operations."""
    full = _build_users_schema()
    paths = {"/users/": full["paths"]["/users/"]}
    spec = build_schema(paths, components=full["components"])
    app = make_flask_app_from_schema(spec)
    _register_users_handlers(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def crud_with_success() -> OpenAPIApp:
    spec = build_schema(
        {**_build_users_schema()["paths"], **schemas.success()},
        components=_build_users_schema().get("components", {}),
    )
    app = make_flask_app_from_schema(spec)
    _register_users_handlers(app)
    handlers.register_success(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


def crud_with_failure() -> OpenAPIApp:
    spec = build_schema(
        {**_build_users_schema()["paths"], **schemas.failure()},
        components=_build_users_schema().get("components", {}),
    )
    app = make_flask_app_from_schema(spec)
    _register_users_handlers(app)
    handlers.register_failure(app)
    return OpenAPIApp(spec=spec, server=app, kind="flask")


class _CreateUser(BaseModel):
    first_name: str = Field(min_length=3)
    last_name: str = Field(min_length=3)
    model_config = ConfigDict(extra="forbid")


class _UpdateUser(BaseModel):
    first_name: str = Field(min_length=3)
    # Planted bug: nullable last_name slips past validation, then GET /users/{id}
    # blows up when concatenating it into a full_name.
    last_name: str | None = Field(None, min_length=3, json_schema_extra={"nullable": True})
    model_config = ConfigDict(extra="forbid")


def crud_asgi() -> OpenAPIApp:
    app = FastAPI()
    users: dict[str, dict[str, Any]] = {}

    @app.post("/users/", status_code=201)
    def create_user(user: _CreateUser) -> dict[str, str]:
        user_id = str(uuid4())
        users[user_id] = {**user.model_dump(), "id": user_id}
        return {"id": user_id}

    @app.get("/users/{user_id}")
    def get_user(user_id: str, uid: str = Query(...), code: int = Query(...)) -> dict[str, str]:
        if user_id not in users:
            raise HTTPException(status_code=404, detail="Not found")
        user = users[user_id]
        try:
            full_name = user["first_name"] + " " + user["last_name"]
        except TypeError as exc:
            raise HTTPException(status_code=500, detail="We got a problem!") from exc
        return {"id": user["id"], "full_name": full_name}

    @app.patch("/users/{user_id}")
    def update_user(user_id: str, update: _UpdateUser, common: int = Query(...)) -> dict[str, Any]:
        if user_id not in users:
            raise HTTPException(status_code=404, detail="Not found")
        user = users[user_id]
        for field in ("first_name", "last_name"):
            user[field] = getattr(update, field)
        return user

    return OpenAPIApp(spec=app.openapi(), server=app, kind="fastapi")
