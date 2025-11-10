from __future__ import annotations

from flask import Flask, jsonify, request

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.runtime import OpenAPIApp

WFC_TOKEN = "secret-token-123"
WFC_SESSION = "sess-abc"
WFC_USERNAME = "alice"
WFC_PASSWORD = "secret"

_PROTECTED = {"/api/protected": {"get": {"responses": {"200": {"description": "OK"}}}}}


def _register_protected(app: Flask) -> None:
    @app.route("/api/protected", methods=["GET"])
    def protected() -> object:
        return jsonify({"ok": True})


def _valid_credentials() -> bool:
    creds = request.get_json(silent=True) or request.form.to_dict()
    return creds.get("username") == WFC_USERNAME and creds.get("password") == WFC_PASSWORD


def wfc_login() -> OpenAPIApp:
    # Rich body covers every token-extraction branch: string, coerced number, null, and non-string.
    spec = build_schema(_PROTECTED)
    app = make_flask_app_from_schema(spec)
    _register_protected(app)

    @app.route("/api/login", methods=["POST"])
    def login() -> object:
        if not _valid_credentials():
            return jsonify({"error": "bad credentials"}), 401
        response = jsonify(
            {"access_token": WFC_TOKEN, "number_token": 42, "null_token": None, "object_token": {"a": 1}}
        )
        response.headers["X-Auth-Token"] = WFC_TOKEN
        response.set_cookie("session", WFC_SESSION)
        return response

    return OpenAPIApp(spec=spec, server=app, kind="flask")


def wfc_login_failing() -> OpenAPIApp:
    spec = build_schema(_PROTECTED)
    app = make_flask_app_from_schema(spec)
    _register_protected(app)

    @app.route("/api/login", methods=["POST"])
    def login() -> object:
        return jsonify({"error": "boom"}), 500

    return OpenAPIApp(spec=spec, server=app, kind="flask")


def wfc_login_plain() -> OpenAPIApp:
    # 200 with a non-JSON body and no cookies.
    spec = build_schema(_PROTECTED)
    app = make_flask_app_from_schema(spec)
    _register_protected(app)

    @app.route("/api/login", methods=["POST"])
    def login() -> object:
        return "not json", 200, {"Content-Type": "text/plain"}

    return OpenAPIApp(spec=spec, server=app, kind="flask")
