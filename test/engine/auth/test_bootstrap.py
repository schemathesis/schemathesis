import json

import pytest
from flask import jsonify, request
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import schemathesis
from schemathesis.config import HttpBearerAuthConfig
from schemathesis.engine import events
from schemathesis.engine.run import PhaseName
from test.engine.auth._helpers import (
    assert_at_least_one_2xx,
    auth_flow_paths,
    auth_flow_security_schemes,
    build_auth_flask_app,
    find_phase_finished,
    open_schema_for_transport,
)


def test_auth_bootstrap_phase_runs_in_default_plan(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app({"/ping": {"get": {"responses": {"200": {"description": "OK"}}}}})
    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
    stream = schemathesis.engine.from_schema(schema).execute()
    seen = [event.phase.name for event in stream if isinstance(event, events.PhaseFinished)]
    assert PhaseName.AUTH_BOOTSTRAP in seen


@pytest.mark.parametrize("transport", ["http", "wsgi"], ids=["http", "wsgi"])
def test_bootstrap_populates_session(transport, ctx, app_runner):
    app, _, _ = build_auth_flask_app(ctx.openapi.make_flask_app)
    schema = open_schema_for_transport(transport, app, app_runner)
    assert schema.analysis.auth_flow is not None

    bootstrap_event = find_phase_finished(schema, PhaseName.AUTH_BOOTSTRAP)
    assert bootstrap_event is not None
    assert bootstrap_event.status.name == "SUCCESS"
    assert bootstrap_event.payload.spec is not None
    assert bootstrap_event.payload.failure_stage is None


def test_protected_endpoint_succeeds_after_bootstrap(ctx, app_runner):
    app, _, _ = build_auth_flask_app(ctx.openapi.make_flask_app)
    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
    protected_statuses: list[int] = []
    for event in schemathesis.engine.from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished):
            for interaction in event.recorder.interactions.values():
                if interaction.request.uri.endswith("/protected"):
                    protected_statuses.append(interaction.response.status_code)
    assert_at_least_one_2xx(protected_statuses, "/protected")


def test_bootstrap_skipped_when_static_auth_present(ctx, app_runner):
    app, _, _ = build_auth_flask_app(ctx.openapi.make_flask_app)
    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
    schema.config.auth.openapi.schemes["BearerAuth"] = HttpBearerAuthConfig(bearer="the-valid-token")

    bootstrap_event = find_phase_finished(schema, PhaseName.AUTH_BOOTSTRAP)
    assert bootstrap_event is not None
    assert bootstrap_event.status.name == "SKIP"
    assert "explicit auth covers" in bootstrap_event.payload.message


@pytest.mark.parametrize("transport", ["http", "wsgi"], ids=["http", "wsgi"])
def test_bootstrap_emits_scenario_events_for_register_and_login(transport, ctx, app_runner):
    # Register and login must surface as ordinary scenarios so cassettes/JUnit/Allure capture them.
    app, _, _ = build_auth_flask_app(ctx.openapi.make_flask_app)
    schema = open_schema_for_transport(transport, app, app_runner)

    started_labels: list[str] = []
    finished_events: list[events.ScenarioFinished] = []
    suite_started_count = 0
    suite_finished_count = 0
    for event in schemathesis.engine.from_schema(schema).execute():
        if isinstance(event, events.SuiteStarted) and event.phase is PhaseName.AUTH_BOOTSTRAP:
            suite_started_count += 1
        elif isinstance(event, events.SuiteFinished) and event.phase is PhaseName.AUTH_BOOTSTRAP:
            suite_finished_count += 1
        elif isinstance(event, events.ScenarioStarted) and event.phase is PhaseName.AUTH_BOOTSTRAP:
            started_labels.append(event.label)
        elif isinstance(event, events.ScenarioFinished) and event.phase is PhaseName.AUTH_BOOTSTRAP:
            finished_events.append(event)

    assert suite_started_count == 1
    assert suite_finished_count == 1
    assert started_labels == ["POST /register", "POST /login"]
    assert [event.label for event in finished_events] == ["POST /register", "POST /login"]
    for event in finished_events:
        assert event.status.name == "SUCCESS"
        assert event.recorder.label == event.label
        cases = list(event.recorder.cases.values())
        assert len(cases) == 1
        interactions = list(event.recorder.interactions.values())
        assert len(interactions) == 1
        interaction = interactions[0]
        assert interaction.response is not None
        assert 200 <= interaction.response.status_code < 300


def test_bootstrap_failed_register_emits_finished_scenario(ctx, app_runner):
    # When register fails, the scenario must still surface so reports show the failed call.
    app, _ = ctx.openapi.make_flask_app(
        auth_flow_paths(),
        components={"securitySchemes": auth_flow_security_schemes()},
    )

    @app.post("/register")
    def register():
        return jsonify({"error": "always rejects"}), 500

    @app.post("/login")
    def login():
        return jsonify({"error": "unreachable"}), 500

    @app.get("/protected")
    def protected():
        return jsonify({"ok": True})

    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")

    finished_events: list[events.ScenarioFinished] = []
    for event in schemathesis.engine.from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished) and event.phase is PhaseName.AUTH_BOOTSTRAP:
            finished_events.append(event)

    # Only the register scenario fires, then bootstrap fails before login.
    assert [event.label for event in finished_events] == ["POST /register"]
    assert finished_events[0].status.name == "FAILURE"
    assert list(finished_events[0].recorder.interactions.values())[0].response.status_code == 500


def test_apikey_protected_endpoint_succeeds_after_bootstrap(ctx, app_runner):
    app, _, _ = build_auth_flask_app(ctx.openapi.make_flask_app, scheme="apikey")
    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
    protected_statuses: list[int] = []
    for event in schemathesis.engine.from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished):
            for interaction in event.recorder.interactions.values():
                if interaction.request.uri.endswith("/protected"):
                    protected_statuses.append(interaction.response.status_code)
    assert_at_least_one_2xx(protected_statuses, "/protected")


_ASGI_AUTH_FLOW_PATHS = auth_flow_paths()
_ASGI_AUTH_FLOW_SECURITY_SCHEMES = auth_flow_security_schemes()


def test_bootstrap_populates_session_via_asgi(ctx):
    schema_dict = ctx.openapi.build_schema(
        _ASGI_AUTH_FLOW_PATHS,
        components={"securitySchemes": _ASGI_AUTH_FLOW_SECURITY_SCHEMES},
    )

    users: dict[str, str] = {}
    valid_token = "the-valid-token"

    async def openapi(_request: Request) -> JSONResponse:
        return JSONResponse(schema_dict)

    async def register(request: Request) -> JSONResponse:
        body = json.loads(await request.body())
        users[body["username"]] = body["password"]
        return JSONResponse({"ok": True})

    async def login(request: Request) -> JSONResponse:
        body = json.loads(await request.body())
        if users.get(body.get("username")) == body.get("password"):
            return JSONResponse({"access_token": valid_token})
        return JSONResponse({"error": "bad creds"}, status_code=401)

    async def protected(request: Request) -> JSONResponse:
        if request.headers.get("authorization") == f"Bearer {valid_token}":
            return JSONResponse({"ok": True})
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    app = Starlette(
        routes=[
            Route("/openapi.json", openapi, methods=["GET"]),
            Route("/register", register, methods=["POST"]),
            Route("/login", login, methods=["POST"]),
            Route("/protected", protected, methods=["GET"]),
        ]
    )

    schema = schemathesis.openapi.from_asgi("/openapi.json", app=app)
    assert schema.analysis.auth_flow is not None

    bootstrap_event = None
    protected_statuses: list[int] = []
    for event in schemathesis.engine.from_schema(schema).execute():
        if isinstance(event, events.PhaseFinished) and event.phase.name is PhaseName.AUTH_BOOTSTRAP:
            bootstrap_event = event
        elif isinstance(event, events.ScenarioFinished):
            for interaction in event.recorder.interactions.values():
                if interaction.request.uri.endswith("/protected"):
                    protected_statuses.append(interaction.response.status_code)
    assert bootstrap_event is not None
    assert bootstrap_event.status.name == "SUCCESS"
    assert bootstrap_event.payload.spec is not None
    assert_at_least_one_2xx(protected_statuses, "/protected via ASGI")


def test_bootstrap_registers_with_every_profile_field(ctx, app_runner):
    # Mirrors Spring APIs that reject blank profile fields and issue unusable tokens when an optional role is unset.
    credentials = {"email": {"type": "string", "minLength": 7}, "password": {"type": "string", "minLength": 8}}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/register": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["firstName", "lastName", "phoneNumber"],
                                    "properties": {
                                        **credentials,
                                        "firstName": {"type": "string"},
                                        "lastName": {"type": "string"},
                                        "phoneNumber": {"type": "string", "minLength": 11, "maxLength": 20},
                                        "userType": {"type": "string", "enum": ["USER", "ADMIN"]},
                                    },
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/login": {
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
            "/protected": {
                "get": {"security": [{"bearerAuth": []}], "responses": {"200": {"description": "OK"}}},
            },
        },
        components={"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}},
    )
    users: dict[str, tuple[str, str | None]] = {}
    tokens: dict[str, str | None] = {}

    @app.post("/register")
    def register():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "bad body"}), 400
        blank = [name for name in ("firstName", "lastName", "phoneNumber") if not str(body.get(name) or "").strip()]
        if blank:
            return jsonify({"error": f"blank: {blank}"}), 400
        users[body["email"]] = (body["password"], body.get("userType"))
        return jsonify({"ok": True})

    @app.post("/login")
    def login():
        body = request.get_json(silent=True) or {}
        user = users.get(body.get("email"))
        if user is None or user[0] != body.get("password"):
            return jsonify({"error": "bad creds"}), 401
        token = f"token-{len(tokens)}"
        tokens[token] = user[1]
        return jsonify({"response": {"accessToken": token}})

    @app.get("/protected")
    def protected():
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if tokens.get(token) is None:
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({"ok": True})

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    bootstrap_event = find_phase_finished(schema, PhaseName.AUTH_BOOTSTRAP)
    assert (bootstrap_event.status.name, bootstrap_event.payload.message) == ("SUCCESS", None)
    assert tokens[schema.bootstrapped_session.token] in ("USER", "ADMIN")
