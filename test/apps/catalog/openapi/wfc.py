from __future__ import annotations

from flask import Flask, jsonify, request

from test.apps.builders import build_schema, make_flask_app_from_schema
from test.apps.runtime import OpenAPIApp

WFC_TOKEN = "secret-token-123"
# Requests a freshly issued token serves before it starts answering 401.
TOKEN_USES = 1
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


WFC_ROLES = ("viewer", "editor", "admin")

_ROLE_GATED = {
    "/api/open": {"get": {"responses": {"200": {"description": "OK"}}}},
    "/api/editor-only/{itemId}": {
        "delete": {
            "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "integer"}}],
            "responses": {
                "204": {"description": "Deleted"},
                "401": {"description": "Unauthorized"},
                "403": {"description": "Forbidden"},
            },
        }
    },
    "/api/admin-only/{itemId}": {
        "delete": {
            "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "integer"}}],
            "responses": {
                "204": {"description": "Deleted"},
                "401": {"description": "Unauthorized"},
                "403": {"description": "Forbidden"},
            },
        }
    },
    "/api/admin-boom/{itemId}": {
        "delete": {
            "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "integer"}}],
            "responses": {
                "204": {"description": "Deleted"},
                "401": {"description": "Unauthorized"},
                "403": {"description": "Forbidden"},
            },
        }
    },
    "/api/nobody/{itemId}": {
        "delete": {
            "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "integer"}}],
            "responses": {
                "204": {"description": "Deleted"},
                "401": {"description": "Unauthorized"},
                "403": {"description": "Forbidden"},
            },
        }
    },
    "/api/validated": {
        "post": {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"amount": {"type": "integer", "minimum": 100}},
                            "required": ["amount"],
                        }
                    }
                },
            },
            "responses": {
                "201": {"description": "Created"},
                "400": {"description": "Bad amount"},
                "401": {"description": "Unauthorized"},
                "403": {"description": "Forbidden"},
            },
        }
    },
}


def wfc_role_gated(deny_status: int = 403) -> OpenAPIApp:
    spec = build_schema(_ROLE_GATED)
    app = make_flask_app_from_schema(spec)

    def role() -> str:
        return (request.headers.get("Authorization") or "").removeprefix("ApiKey ").strip()

    @app.route("/api/open", methods=["GET"])
    def open_endpoint() -> object:
        return jsonify({"ok": True})

    @app.route("/api/editor-only/<item_id>", methods=["DELETE"])
    def editor_only(item_id: str) -> object:
        if role() not in ("editor", "admin"):
            return jsonify({"detail": "denied"}), deny_status
        return "", 204

    @app.route("/api/admin-only/<item_id>", methods=["DELETE"])
    def admin_only(item_id: str) -> object:
        if role() != "admin":
            return jsonify({"detail": "denied"}), deny_status
        return "", 204

    @app.route("/api/admin-boom/<item_id>", methods=["DELETE"])
    def admin_boom(item_id: str) -> object:
        if role() != "admin":
            return jsonify({"detail": "denied"}), deny_status
        raise RuntimeError("boom")

    @app.route("/api/nobody/<item_id>", methods=["DELETE"])
    def nobody(item_id: str) -> object:
        return jsonify({"detail": "denied"}), deny_status

    @app.route("/api/validated", methods=["POST"])
    def validated() -> object:
        # Payload is checked before the role, so a wrong identity sees 400 interleaved with the denial.
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("amount"), int) or body["amount"] < 100:
            return jsonify({"detail": "bad amount"}), 400
        if role() != "admin":
            return jsonify({"detail": "denied"}), deny_status
        return jsonify({"ok": True}), 201

    return OpenAPIApp(spec=spec, server=app, kind="flask")


def wfc_role_gated_401() -> OpenAPIApp:
    return wfc_role_gated(deny_status=401)


def wfc_expiring_token() -> OpenAPIApp:
    """Login issues a token that dies after `TOKEN_USES` requests; re-login mints a fresh one."""
    # Schema examples give the operation a fixed number of requests, so the token expires regardless
    # of how many cases the generator happens to draw on a given platform.
    spec = build_schema(
        {
            "/api/protected": {
                "get": {
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "schema": {"type": "integer"},
                            "examples": {str(i): {"value": i} for i in range(1, 6)},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}, "401": {"description": "Token expired"}},
                }
            }
        }
    )
    app = make_flask_app_from_schema(spec)
    issued: dict[str, int] = {}
    counter = {"n": 0}

    @app.route("/api/login", methods=["POST"])
    def login() -> object:
        creds = request.get_json(silent=True) or request.form.to_dict()
        counter["n"] += 1
        token = f"{creds.get('user', '?')}-{counter['n']}"
        issued[token] = 0
        return jsonify({"access_token": token})

    @app.route("/api/protected", methods=["GET"])
    def protected() -> object:
        token = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        used = issued.get(token)
        if used is None or used >= TOKEN_USES:
            return jsonify({"detail": "token expired"}), 401
        issued[token] = used + 1
        return jsonify({"ok": True})

    return OpenAPIApp(spec=spec, server=app, kind="flask")


def wfc_credentials_rejected() -> OpenAPIApp:
    """Every credential is invalid; the endpoint serves anonymous callers and 401s any `Authorization`."""
    spec = build_schema(
        {
            "/api/public/{itemId}": {
                "get": {
                    "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorized"}},
                }
            }
        }
    )
    app = make_flask_app_from_schema(spec)

    @app.route("/api/public/<item_id>", methods=["GET"])
    def public(item_id: str) -> object:
        if request.headers.get("Authorization"):
            return jsonify({"detail": "Bad credentials"}), 401
        return jsonify({"ok": True})

    return OpenAPIApp(spec=spec, server=app, kind="flask")


def wfc_role_gated_401_when_anonymous() -> OpenAPIApp:
    """No identity is authorized, and dropping credentials only moves the refusal earlier."""
    spec = build_schema(_ROLE_GATED)
    app = make_flask_app_from_schema(spec)

    def refuse() -> object:
        if not request.headers.get("Authorization"):
            return jsonify({"detail": "unauthenticated"}), 401
        return jsonify({"detail": "denied"}), 403

    @app.route("/api/open", methods=["GET"])
    def open_endpoint() -> object:
        return jsonify({"ok": True})

    @app.route("/api/nobody/<item_id>", methods=["DELETE"])
    def nobody(item_id: str) -> object:
        return refuse()

    for path, name in (
        ("/api/editor-only/<item_id>", "editor_only"),
        ("/api/admin-only/<item_id>", "admin_only"),
        ("/api/admin-boom/<item_id>", "admin_boom"),
    ):
        app.add_url_rule(path, name, refuse, methods=["DELETE"])

    @app.route("/api/validated", methods=["POST"])
    def validated() -> object:
        return refuse()

    return OpenAPIApp(spec=spec, server=app, kind="flask")
