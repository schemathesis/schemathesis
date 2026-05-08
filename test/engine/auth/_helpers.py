from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from flask import jsonify, request

import schemathesis
from schemathesis.engine import events

if TYPE_CHECKING:
    from collections.abc import Mapping

    from flask import Flask

    from schemathesis.engine.run import PhaseName
    from schemathesis.schemas import BaseSchema


_USERNAME_PASSWORD_BODY = {
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["username", "password"],
                "properties": {
                    "username": {"type": "string"},
                    "password": {"type": "string"},
                },
            }
        }
    }
}

_LOGIN_OK_RESPONSE = {
    "200": {
        "description": "OK",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {"access_token": {"type": "string"}},
                }
            }
        },
    }
}


def auth_flow_paths(
    *,
    scheme: str = "bearer",
    extra_paths: Mapping[str, Any] | None = None,
    password_schema: Mapping[str, Any] | None = None,
    register_responses: Mapping[str, Any] | None = None,
    include_protected: bool = True,
) -> dict[str, Any]:
    """Standard register/login (and optional /protected) paths for the auth-flow tests.

    `scheme` selects between "bearer" and "apikey" security schemes.
    `extra_paths` is merged in for tests that need additional operations (e.g. /pets).
    `password_schema` overrides the password property schema (mint-failure tests).
    `register_responses` overrides /register's responses block.
    `include_protected` controls whether GET /protected is part of the schema.
    """
    requirement_name = "BearerAuth" if scheme == "bearer" else "ApiKey"

    register_body = deepcopy(_USERNAME_PASSWORD_BODY)
    if password_schema is not None:
        register_body["content"]["application/json"]["schema"]["properties"]["password"] = dict(password_schema)

    paths: dict[str, Any] = {
        "/register": {
            "post": {
                "requestBody": register_body,
                "responses": dict(register_responses) if register_responses else {"200": {"description": "OK"}},
            }
        },
        "/login": {
            "post": {
                "requestBody": deepcopy(_USERNAME_PASSWORD_BODY),
                "responses": deepcopy(_LOGIN_OK_RESPONSE),
                "security": [{requirement_name: []}],
            }
        },
    }
    if include_protected:
        paths["/protected"] = {
            "get": {
                "security": [{requirement_name: []}],
                "responses": {"200": {"description": "OK"}},
            }
        }
    if extra_paths:
        for path, definition in extra_paths.items():
            paths[path] = definition
    return paths


def auth_flow_security_schemes(scheme: str = "bearer") -> dict[str, Any]:
    if scheme == "bearer":
        return {"BearerAuth": {"type": "http", "scheme": "bearer"}}
    return {"ApiKey": {"type": "apiKey", "name": "X-Api-Key", "in": "header"}}


def build_auth_flask_app(
    make_flask_app,
    *,
    scheme: str = "bearer",
    extra_paths: Mapping[str, Any] | None = None,
    password_schema: Mapping[str, Any] | None = None,
    register_responses: Mapping[str, Any] | None = None,
    include_protected: bool = True,
    valid_token: str | None = None,
) -> tuple[Flask, dict[str, str], str]:
    """Build a Flask app implementing register/login (and optional /protected).

    Returns `(app, users, valid_token)` so tests can inspect server-side state
    (the `users` dict accumulates registered credentials, `valid_token` is the
    token issued by /login on success).
    """
    if valid_token is None:
        valid_token = "the-valid-token" if scheme == "bearer" else "the-valid-key"
    paths = auth_flow_paths(
        scheme=scheme,
        extra_paths=extra_paths,
        password_schema=password_schema,
        register_responses=register_responses,
        include_protected=include_protected,
    )
    app, _ = make_flask_app(
        paths,
        components={"securitySchemes": auth_flow_security_schemes(scheme)},
    )
    users: dict[str, str] = {}

    @app.post("/register")
    def register():
        body = request.get_json() or {}
        users[body["username"]] = body["password"]
        return jsonify({"ok": True})

    @app.post("/login")
    def login():
        body = request.get_json() or {}
        if users.get(body.get("username")) == body.get("password"):
            return jsonify({"access_token": valid_token})
        return jsonify({"error": "bad creds"}), 401

    if include_protected:
        if scheme == "bearer":

            @app.get("/protected")
            def protected():
                if request.headers.get("Authorization") == f"Bearer {valid_token}":
                    return jsonify({"ok": True})
                return jsonify({"error": "unauthorized"}), 401
        else:

            @app.get("/protected")
            def protected():
                if request.headers.get("X-Api-Key") == valid_token:
                    return jsonify({"ok": True})
                return jsonify({"error": "unauthorized"}), 401

    return app, users, valid_token


def find_phase_finished(schema: BaseSchema, phase_name: PhaseName) -> events.PhaseFinished | None:
    """Return the first PhaseFinished event matching the given phase, or None."""
    for event in schemathesis.engine.from_schema(schema).execute():
        if isinstance(event, events.PhaseFinished) and event.phase.name is phase_name:
            return event
    return None


def assert_at_least_one_2xx(statuses: list[int], label: str) -> None:
    assert statuses, f"Expected at least one {label} request"
    assert any(200 <= status < 300 for status in statuses), (
        f"Expected at least one 2xx {label} response, got: {statuses}"
    )


def open_schema_for_transport(transport: str, app, app_runner) -> BaseSchema:
    if transport == "http":
        port = app_runner.run_flask_app(app)
        return schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
    if transport == "wsgi":
        return schemathesis.openapi.from_wsgi("/openapi.json", app=app)
    raise ValueError(f"unknown transport: {transport}")
