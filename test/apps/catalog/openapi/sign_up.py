from __future__ import annotations

from typing import Literal

import flask
from flask import jsonify

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.runtime import OpenAPIApp

SignUpBehavior = Literal[
    "ok",
    "undeclared-security",
    "register-rejects",
    "login-rejects",
    "no-token",
    "unsatisfiable-password",
    "admin-only",
    "ordinary-role",
]


def sign_up_and_login(behavior: SignUpBehavior = "ok") -> OpenAPIApp:
    """Sign-up requires non-blank profile fields; tokens work only for accounts registered with a role."""
    password = (
        {"type": "string", "minLength": 8, "maxLength": 4}
        if behavior == "unsatisfiable-password"
        else {"type": "string", "minLength": 8}
    )
    credentials = {"email": {"type": "string", "minLength": 5}, "username": {"type": "string"}, "password": password}
    spec = build_schema(
        {
            "/auth/register": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["email", "password", "firstName"],
                                    "properties": {
                                        **credentials,
                                        "firstName": {"type": "string"},
                                        "role": {
                                            "type": "string",
                                            "enum": ["MEMBER"] if behavior == "ordinary-role" else ["USER", "ADMIN"],
                                        },
                                    },
                                }
                            }
                        }
                    },
                    "responses": {"201": {"description": "Created"}},
                }
            },
            "/auth/login": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["email", "password"],
                                    "properties": credentials,
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "response": {
                                                "type": "object",
                                                "properties": {"accessToken": {"type": "string"}},
                                            }
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/orders": {
                "get": {
                    # Servers often enforce auth on operations whose schema declares none.
                    "security": [] if behavior == "undeclared-security" else [{"bearerAuth": []}],
                    "responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorized"}},
                }
            },
        },
        components={"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}},
    )
    app = make_flask_app_from_schema(spec)
    users: dict[str, tuple[str, str | None]] = {}
    tokens: dict[str, str | None] = {}

    @app.post("/auth/register")
    def register():
        body = flask.request.get_json(silent=True)
        if behavior == "register-rejects" or not isinstance(body, dict):
            return jsonify({"error": "always bad"}), 400
        if not str(body.get("firstName") or "").strip():
            return jsonify({"error": "firstName can't be blank"}), 400
        users[str(body.get("email"))] = (str(body.get("password")), body.get("role"))
        return jsonify({}), 201

    @app.post("/auth/login")
    def login():
        body = flask.request.get_json(silent=True) or {}
        user = users.get(str(body.get("email")))
        if behavior == "login-rejects" or user is None or user[0] != body.get("password"):
            return jsonify({"error": "bad credentials"}), 401
        if behavior == "no-token":
            return "logged in", 200, {"Content-Type": "text/plain"}
        token = f"token-{len(tokens)}"
        tokens[token] = user[1]
        return jsonify({"response": {"accessToken": token}})

    @app.get("/orders")
    def orders():
        token = flask.request.headers.get("Authorization", "").removeprefix("Bearer ")
        if tokens.get(token) is None:
            return jsonify({"error": "unauthorized"}), 401
        if behavior == "admin-only" and tokens[token] != "ADMIN":
            return jsonify({"error": "forbidden"}), 403
        return jsonify([])

    return OpenAPIApp(spec=spec, server=app, kind="flask")
