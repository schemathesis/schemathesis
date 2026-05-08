import pytest
from flask import jsonify, request


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_full_run_flight_search_shape(ctx, app_runner, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/v1/auth/register": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["username", "password", "email"],
                                    "properties": {
                                        "username": {"type": "string"},
                                        "password": {"type": "string"},
                                        "email": {"type": "string"},
                                    },
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/api/v1/auth/login": {
                "post": {
                    "requestBody": {
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
                    },
                    "responses": {
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
                    },
                    "security": [{"BearerAuth": []}],
                }
            },
            "/api/v1/flights": {
                "get": {
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
        components={"securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}}},
    )

    users: dict[str, str] = {}
    valid_token = "valid-jwt-12345"
    flights_authorization_headers: list[str | None] = []

    @app.post("/api/v1/auth/register")
    def register():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({"error": "bad body"}), 400
        try:
            users[body["username"]] = body["password"]
        except (KeyError, TypeError):
            return jsonify({"error": "missing fields"}), 400
        return jsonify({"ok": True})

    @app.post("/api/v1/auth/login")
    def login():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({"error": "bad body"}), 400
        if users.get(body.get("username")) == body.get("password"):
            return jsonify({"access_token": valid_token})
        return jsonify({"error": "bad creds"}), 401

    @app.get("/api/v1/flights")
    def flights():
        flights_authorization_headers.append(request.headers.get("Authorization"))
        if request.headers.get("Authorization") == f"Bearer {valid_token}":
            return jsonify([])
        return jsonify({"error": "unauthorized"}), 401

    port = app_runner.run_flask_app(app)
    schema_url = f"http://127.0.0.1:{port}/openapi.json"
    assert cli.run(schema_url, "--max-examples=10", "--seed=42") == snapshot_cli
    assert f"Bearer {valid_token}" in flights_authorization_headers, (
        f"expected at least one /api/v1/flights call carrying 'Bearer {valid_token}', got: {flights_authorization_headers!r}"
    )
