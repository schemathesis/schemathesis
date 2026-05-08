from flask import jsonify, request

import schemathesis
from schemathesis.engine import events
from schemathesis.engine.run import PhaseName
from test.engine.auth._helpers import (
    auth_flow_paths,
    auth_flow_security_schemes,
    build_auth_flask_app,
)


def test_login_succeeds_during_regular_fuzzing(ctx, app_runner):
    # Without credential injection, login fuzzing produces only random Unicode the SUT rejects.
    app, _, _ = build_auth_flask_app(ctx.openapi.make_flask_app)
    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")

    login_2xx_count = 0
    for event in schemathesis.engine.from_schema(schema).execute():
        if hasattr(event, "recorder") and event.recorder is not None:
            for case_id, interaction in event.recorder.interactions.items():
                if interaction.response is not None and 200 <= interaction.response.status_code < 300:
                    case = event.recorder.cases.get(case_id)
                    if case is not None and case.value.operation.label == "POST /login":
                        login_2xx_count += 1
    assert login_2xx_count >= 1, "expected at least one POST /login 2xx during fuzzing"


def test_unrelated_operation_does_not_receive_credentials(ctx, app_runner):
    # Operations outside the auth flow must not receive bootstrapped credential values.
    received_pet_bodies: list[dict[str, str]] = []
    pets_path = {
        "/pets": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["username"],
                                "properties": {"username": {"type": "string"}},
                            }
                        }
                    }
                },
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    app, _ = ctx.openapi.make_flask_app(
        auth_flow_paths(extra_paths=pets_path, include_protected=False),
        components={"securitySchemes": auth_flow_security_schemes()},
    )
    users: dict[str, str] = {}

    @app.post("/register")
    def register():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or "username" not in body or "password" not in body:
            return jsonify({"error": "bad input"}), 400
        users[body["username"]] = body["password"]
        return jsonify({"ok": True})

    @app.post("/login")
    def login():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "bad input"}), 400
        if users.get(body.get("username")) == body.get("password"):
            return jsonify({"access_token": "tok"})
        return jsonify({"error": "bad creds"}), 401

    @app.post("/pets")
    def create_pet():
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            received_pet_bodies.append(body)
        return jsonify({"ok": True})

    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")

    bootstrapped_username: str | None = None
    for event in schemathesis.engine.from_schema(schema).execute():
        if (
            isinstance(event, events.PhaseFinished)
            and event.phase.name is PhaseName.AUTH_BOOTSTRAP
            and event.payload is not None
            and event.payload.spec is not None
        ):
            bootstrapped_username = next(iter(users), None) if users else None

    assert bootstrapped_username is not None
    assert received_pet_bodies, "expected at least one POST /pets call to verify credential isolation"
    pet_usernames = {body.get("username") for body in received_pet_bodies}
    assert bootstrapped_username not in pet_usernames, (
        f"bootstrapped username {bootstrapped_username!r} leaked into /pets bodies"
    )


def test_negative_mode_uses_bootstrapped_credentials_as_base(ctx, app_runner):
    # Negative-mode register/login bodies start from real credentials with one field mutated, not random unicode.
    received_register_bodies: list[dict] = []
    bootstrapped_credentials: dict[str, str] = {}
    app, _ = ctx.openapi.make_flask_app(
        auth_flow_paths(include_protected=False),
        components={"securitySchemes": auth_flow_security_schemes()},
    )
    users: dict[str, str] = {}

    @app.post("/register")
    def register():
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            received_register_bodies.append(body)
        if not isinstance(body, dict) or "username" not in body or "password" not in body:
            return jsonify({"error": "bad input"}), 400
        users[body["username"]] = body["password"]
        return jsonify({"ok": True})

    @app.post("/login")
    def login():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "bad input"}), 400
        if users.get(body.get("username")) == body.get("password"):
            return jsonify({"access_token": "tok"})
        return jsonify({"error": "bad creds"}), 401

    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")

    for event in schemathesis.engine.from_schema(schema).execute():
        if (
            isinstance(event, events.PhaseFinished)
            and event.phase.name is PhaseName.AUTH_BOOTSTRAP
            and event.payload is not None
            and event.payload.spec is not None
            and users
            and not bootstrapped_credentials
        ):
            username = next(iter(users))
            bootstrapped_credentials["username"] = username
            bootstrapped_credentials["password"] = users[username]

    assert bootstrapped_credentials, "expected bootstrap to register at least one credential pair"
    assert received_register_bodies, "expected at least one POST /register call to verify"
    bootstrapped_username = bootstrapped_credentials["username"]
    bootstrapped_password = bootstrapped_credentials["password"]
    overlay_bodies = [
        body
        for body in received_register_bodies
        if isinstance(body, dict)
        and (body.get("username") == bootstrapped_username or body.get("password") == bootstrapped_password)
    ]
    assert overlay_bodies, (
        "expected at least one register body to carry a bootstrapped credential value — "
        "if all are random unicode in negative mode, credential injection is broken"
    )
