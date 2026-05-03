from __future__ import annotations

from dataclasses import dataclass, field

import flask
from flask import jsonify

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.runtime import Modifier, OpenAPIApp


@dataclass
class UnderDeclaredSecurityConfig:
    valid_token: str = "real-token"
    # Status the handler returns when the bearer token matches; the spec only documents 200/401.
    authed_status: int = 200


@dataclass
class UnderDeclaredSecurityStore:
    config: UnderDeclaredSecurityConfig = field(default_factory=UnderDeclaredSecurityConfig)


def under_declared_security(*modifiers: Modifier[UnderDeclaredSecurityStore]) -> OpenAPIApp:
    """Schema declares `/protected` as public, but the server enforces BearerAuth."""
    spec = build_schema(
        {
            "/protected": {
                "get": {
                    "responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorized"}},
                }
            }
        },
        components={"securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}}},
    )
    app = make_flask_app_from_schema(spec)
    app.config["schema"] = spec
    store = UnderDeclaredSecurityStore()

    @app.get("/protected")
    def protected():
        if flask.request.headers.get("Authorization") == f"Bearer {store.config.valid_token}":
            return jsonify({"ok": True}), store.config.authed_status
        return jsonify({"error": "no auth"}), 401

    for modifier in sorted(modifiers, key=lambda m: m.priority):
        modifier.apply(app, store)
    return OpenAPIApp(spec=spec, server=app, kind="flask")
