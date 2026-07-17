from __future__ import annotations

import itertools
import socket

import pytest
import requests
from flask import jsonify, request

import schemathesis.openapi
from schemathesis.auths import (
    REAUTH_BREAKER_THRESHOLD,
    TOKEN_FETCH_BREAKER_THRESHOLD,
    AuthContext,
    CachingAuthProvider,
    ReauthState,
    reauth_and_replay,
    refresh_auth,
    set_on_case,
)
from schemathesis.config._auth import AuthConfig, DynamicTokenAuthConfig
from schemathesis.config._error import ConfigError
from schemathesis.core.errors import AuthenticationError
from schemathesis.specs.openapi.adapter.security import build_auth_provider
from schemathesis.specs.openapi.auths import (
    ApiKeyAuthProvider,
    DynamicTokenAuthProvider,
    HttpBearerAuthProvider,
)

OAUTH2_SCHEME = {"type": "oauth2", "flows": {"password": {"tokenUrl": "/api/auth", "scopes": {}}}}


def _dynamic_auth(scheme, **overrides):
    return {"dynamic": {"openapi": {scheme: {"path": "/api/auth", "extract_selector": "/access_token", **overrides}}}}


def _register_single_use_token(app):
    # Token is valid for exactly one request, so every later call must re-authenticate.
    state = {"issued": 0, "valid": None}

    @app.route("/api/auth", methods=["POST"])
    def auth_endpoint():
        state["issued"] += 1
        state["valid"] = f"tok{state['issued']}"
        return jsonify({"access_token": state["valid"]})

    @app.route("/protected")
    def protected():
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token != state["valid"]:
            return jsonify({"error": "unauthorized"}), 401
        state["valid"] = None
        return jsonify({"result": "ok"})

    return state


@pytest.mark.parametrize(
    "path,extract_from,extract_selector,expected",
    [
        ("/api/body-auth", "body", "/access_token", "test-token"),
        ("/api/nested-auth", "body", "/data/token", "test-token"),
        ("/api/header-auth", "header", "X-Auth-Token", "test-token"),
        ("/api/bad-charset-auth/bogus-xyz", "body", "/access_token", "test-token"),
        ("/api/bad-charset-auth/undefined", "body", "/access_token", "test-token"),
        ("/api/bom-auth", "body", "/access_token", "test-token"),
        # Non-UTF-8 body with a truthfully declared legacy charset: `é` must survive, not become U+FFFD.
        ("/api/latin1-auth", "body", "/access_token", "café-token"),
    ],
)
def test_get_extracts_token(auth_operation, path, extract_from, extract_selector, expected):
    ctx = AuthContext(operation=auth_operation, app=None)
    provider = DynamicTokenAuthProvider(
        path=path,
        method="post",
        payload=None,
        extract_from=extract_from,
        extract_selector=extract_selector,
        _applier=HttpBearerAuthProvider(bearer=""),
    )
    assert provider.get(auth_operation.Case(), ctx) == expected


@pytest.mark.parametrize(
    "path,extract_from,extract_selector,match",
    [
        ("/api/fail", "body", "/access_token", "401"),
        ("/api/missing-key", "body", "/missing", "/missing"),
        ("/api/body-auth", "header", "X-Missing", "X-Missing"),
        ("/api/non-json", "body", "/token", "non-JSON"),
        ("/api/wrong-type", "body", "/token", "Expected a string"),
    ],
)
def test_get_raises_on_error(auth_operation, path, extract_from, extract_selector, match):
    ctx = AuthContext(operation=auth_operation, app=None)
    provider = DynamicTokenAuthProvider(
        path=path,
        method="post",
        payload=None,
        extract_from=extract_from,
        extract_selector=extract_selector,
        _applier=HttpBearerAuthProvider(bearer=""),
    )
    with pytest.raises(AuthenticationError, match=match):
        provider.get(auth_operation.Case(), ctx)


def test_authentication_error_provider_context():
    assert str(AuthenticationError("DynamicTokenAuthProvider", "get", "boom", include_common_causes=False)) == "boom"
    assert (
        str(AuthenticationError("MyAuth", "get", "boom", include_common_causes=False, include_provider_context=True))
        == "Error in 'MyAuth.get()': boom"
    )


def test_caching_provider_does_not_double_wrap_auth_error(auth_operation):
    inner = DynamicTokenAuthProvider(
        path="/api/fail",
        method="post",
        payload=None,
        extract_from="body",
        extract_selector="/access_token",
        _applier=HttpBearerAuthProvider(bearer=""),
    )
    ctx = AuthContext(operation=auth_operation, app=None)
    with pytest.raises(AuthenticationError) as direct:
        inner.get(auth_operation.Case(), ctx)
    with pytest.raises(AuthenticationError) as cached:
        CachingAuthProvider(inner).get(auth_operation.Case(), ctx)
    assert str(cached.value) == str(direct.value)


def test_get_401_message_is_actionable_without_common_causes(auth_operation):
    provider = DynamicTokenAuthProvider(
        path="/api/fail",
        method="post",
        payload=None,
        extract_from="body",
        extract_selector="/access_token",
        _applier=HttpBearerAuthProvider(bearer=""),
    )
    with pytest.raises(AuthenticationError) as exc:
        provider.get(auth_operation.Case(), AuthContext(operation=auth_operation, app=None))
    assert str(exc.value) == (
        "Auth endpoint rejected the credentials. Check the configured auth credentials.\n"
        "\n[401] Unauthorized:\n"
        '\n    `{"error":"unauthorized"}`'
    )


def test_fetch_http_forwards_tls_config(ctx, app_runner, mocker):
    app, _ = ctx.openapi.make_flask_app({"/data": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/api/auth", methods=["POST"])
    def auth():
        return jsonify({"access_token": "test-token"})

    spy = mocker.patch("requests.request", wraps=requests.request)
    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    schema.config.tls_verify = False
    operation = schema["/data"]["GET"]
    provider = DynamicTokenAuthProvider(
        path="/api/auth",
        method="post",
        payload=None,
        extract_from="body",
        extract_selector="/access_token",
        _applier=HttpBearerAuthProvider(bearer=""),
    )
    assert provider.get(operation.Case(), AuthContext(operation=operation, app=None)) == "test-token"
    assert spy.call_args[1]["verify"] is False


def test_get_raises_on_connection_error(ctx, cli, app_runner):
    app, _ = ctx.openapi.make_flask_app({"/data": {"get": {"responses": {"200": {"description": "OK"}}}}})
    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    operation = schema["/data"]["GET"]

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]
    schema.config.base_url = f"http://127.0.0.1:{closed_port}"

    auth_ctx = AuthContext(operation=operation, app=None)
    provider = DynamicTokenAuthProvider(
        path="/api/auth",
        method="post",
        payload=None,
        extract_from="body",
        extract_selector="/token",
        _applier=HttpBearerAuthProvider(bearer=""),
    )
    with pytest.raises(AuthenticationError, match="Connection to auth endpoint failed"):
        provider.get(operation.Case(), auth_ctx)


@pytest.mark.parametrize(
    "applier,token,expected_header,expected_value",
    [
        (HttpBearerAuthProvider(bearer=""), "my-token", "Authorization", "Bearer my-token"),
        (ApiKeyAuthProvider(value="", name="X-API-Key", location="header"), "my-key", "X-API-Key", "my-key"),
    ],
)
def test_set_applies_token(auth_operation, applier, token, expected_header, expected_value):
    case = auth_operation.Case()
    provider = DynamicTokenAuthProvider(
        path="/api/body-auth",
        method="post",
        payload=None,
        extract_from="body",
        extract_selector="/access_token",
        _applier=applier,
    )
    provider.set(case, token, None)
    assert case.headers[expected_header] == expected_value


def test_api_key_auth_unknown_location_is_noop(auth_operation):
    case = auth_operation.Case()
    provider = ApiKeyAuthProvider(value="", name="X-API-Key", location="unknown")

    provider.set(case, "secret", None)

    assert "X-API-Key" not in case.headers
    assert "X-API-Key" not in case.query
    assert "X-API-Key" not in case.cookies


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"path": "api/auth", "extract_selector": "/token"}, "must start with '/'"),
        (
            {"path": "/api/auth", "extract_from": "body", "extract_selector": "access_token"},
            "extract_selector.*must start with '/'",
        ),
        (
            {"path": "/api/auth", "extract_selector": "/token", "payload_content_type": ""},
            "payload_content_type.*non-empty",
        ),
    ],
)
def test_config_rejects_invalid_fields(kwargs, match):
    with pytest.raises(ConfigError, match=match):
        DynamicTokenAuthConfig(**kwargs)


@pytest.fixture
def form_auth_app(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {"/data": {"get": {"operationId": "getData", "responses": {"200": {"description": "OK"}}}}}
    )
    received: dict = {}

    @app.route("/api/form-auth", methods=["POST"])
    def form_auth():
        received["content_type"] = request.content_type
        received["form"] = dict(request.form)
        received["raw"] = request.get_data(as_text=True)
        return jsonify({"access_token": "form-token"})

    return app, received


def test_form_payload_sent_as_urlencoded(form_auth_app, app_runner):
    app, received = form_auth_app
    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    operation = schema["/data"]["GET"]
    provider = DynamicTokenAuthProvider(
        path="/api/form-auth",
        method="post",
        payload={"grant_type": "password", "username": "alice", "password": "s3cret"},
        payload_content_type="application/x-www-form-urlencoded",
        extract_from="body",
        extract_selector="/access_token",
        _applier=HttpBearerAuthProvider(bearer=""),
    )
    assert provider.get(operation.Case(), AuthContext(operation=operation, app=None)) == "form-token"
    assert received == {
        "content_type": "application/x-www-form-urlencoded",
        "form": {"grant_type": "password", "username": "alice", "password": "s3cret"},
        "raw": "grant_type=password&username=alice&password=s3cret",
    }


def test_json_payload_default_unchanged(form_auth_app, app_runner):
    app, received = form_auth_app

    @app.route("/api/json-auth", methods=["POST"])
    def json_auth():
        received["content_type"] = request.content_type
        received["json"] = request.get_json(silent=True)
        return jsonify({"access_token": "json-token"})

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    operation = schema["/data"]["GET"]
    provider = DynamicTokenAuthProvider(
        path="/api/json-auth",
        method="post",
        payload={"username": "alice", "password": "s3cret"},
        payload_content_type="application/json",
        extract_from="body",
        extract_selector="/access_token",
        _applier=HttpBearerAuthProvider(bearer=""),
    )
    assert provider.get(operation.Case(), AuthContext(operation=operation, app=None)) == "json-token"
    assert received == {
        "content_type": "application/json",
        "json": {"username": "alice", "password": "s3cret"},
    }


def test_custom_json_variant_content_type(form_auth_app, app_runner):
    app, received = form_auth_app

    @app.route("/api/vnd-auth", methods=["POST"])
    def vnd_auth():
        received["content_type"] = request.content_type
        received["json"] = request.get_json(silent=True, force=True)
        return jsonify({"access_token": "vnd-token"})

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    operation = schema["/data"]["GET"]
    provider = DynamicTokenAuthProvider(
        path="/api/vnd-auth",
        method="post",
        payload={"key": "value"},
        payload_content_type="application/vnd.api+json; charset=utf-8",
        extract_from="body",
        extract_selector="/access_token",
        _applier=HttpBearerAuthProvider(bearer=""),
    )
    assert provider.get(operation.Case(), AuthContext(operation=operation, app=None)) == "vnd-token"
    assert received == {
        "content_type": "application/vnd.api+json; charset=utf-8",
        "json": {"key": "value"},
    }


def test_unsupported_content_type_raises_at_runtime(form_auth_app, app_runner):
    app, _ = form_auth_app
    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    operation = schema["/data"]["GET"]
    provider = DynamicTokenAuthProvider(
        path="/api/form-auth",
        method="post",
        payload={"key": "value"},
        payload_content_type="application/xml",
        extract_from="body",
        extract_selector="/access_token",
        _applier=HttpBearerAuthProvider(bearer=""),
    )
    with pytest.raises(AuthenticationError, match="Unsupported payload_content_type 'application/xml'"):
        provider.get(operation.Case(), AuthContext(operation=operation, app=None))


@pytest.mark.parametrize(
    "openapi_schemes,dynamic_schemes,match",
    [
        (
            {"BearerAuth": {"bearer": "token"}},
            {"BearerAuth": {"path": "/api/auth", "extract_selector": "/token"}},
            "Scheme 'BearerAuth' appears",
        ),
        (
            {"BearerAuth": {"bearer": "token"}, "ApiKeyAuth": {"api_key": "key"}},
            {
                "BearerAuth": {"path": "/api/auth", "extract_selector": "/token"},
                "ApiKeyAuth": {"path": "/api/auth", "extract_selector": "/token"},
            },
            "Schemes .* appear",
        ),
    ],
)
def test_config_rejects_overlapping_schemes(openapi_schemes, dynamic_schemes, match):
    with pytest.raises(ConfigError, match=match):
        AuthConfig(
            openapi=openapi_schemes,
            dynamic={"openapi": dynamic_schemes},
        )


@pytest.mark.parametrize(
    "scheme,match",
    [
        ({"type": "http", "scheme": "basic"}, "http/basic"),
    ],
)
def test_build_auth_provider_rejects_unsupported_scheme(scheme, match):
    config = DynamicTokenAuthConfig(path="/api/auth", extract_selector="/token")
    with pytest.raises(ConfigError, match=match):
        build_auth_provider(config, scheme)


@pytest.mark.parametrize(
    "scheme",
    [
        OAUTH2_SCHEME,
        {"type": "openIdConnect", "openIdConnectUrl": "https://example.com/.well-known/openid-configuration"},
    ],
    ids=["oauth2", "openIdConnect"],
)
def test_build_auth_provider_applies_bearer_token_schemes(auth_operation, scheme):
    config = DynamicTokenAuthConfig(path="/api/auth", extract_selector="/access_token")
    provider = build_auth_provider(config, scheme)
    case = auth_operation.Case()
    provider.set(case, "my-token", None)
    assert case.headers["Authorization"] == "Bearer my-token"


def test_build_auth_provider_carries_configured_retry_on():
    config = DynamicTokenAuthConfig(path="/api/auth", extract_selector="/access_token", retry_on=[401, 419])
    provider = build_auth_provider(config, {"type": "http", "scheme": "bearer"})
    assert provider.retry_on == [401, 419]


def test_reauth_state_not_shared_across_calls(ctx):
    # Each `call_and_validate` gets a fresh breaker; one call tripping the breaker must not
    # disable reactive refresh for later, independent calls that share the schema.
    schema = ctx.openapi.load_schema({"/a": {"get": {"responses": {"200": {"description": "OK"}}}}})
    tripped = schema.reauth_state
    for _ in range(REAUTH_BREAKER_THRESHOLD):
        tripped.note_refresh_failure()
    assert tripped.disabled is True
    assert schema.reauth_state.disabled is False


def test_unused_dynamic_auth_warning(ctx, cli, app_runner, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/data": {
                "get": {
                    "operationId": "getData",
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "securitySchemes": {
                "BearerAuth": {"type": "http", "scheme": "bearer"},
            }
        },
    )
    base_url = app_runner.openapi_url(app, path="")
    assert (
        cli.run(
            f"{base_url}/openapi.json",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 1",
            config={
                "base-url": base_url,
                "auth": _dynamic_auth("NonExistentAuth"),
            },
        )
        == snapshot_cli
    )


def test_dynamic_auth_integration(ctx, cli, app_runner, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/protected": {
                "get": {
                    "operationId": "getProtected",
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorized"}},
                }
            }
        },
        components={
            "securitySchemes": {
                "BearerAuth": {"type": "http", "scheme": "bearer"},
            }
        },
    )

    @app.route("/api/auth", methods=["POST"])
    def auth_endpoint():
        return jsonify({"access_token": "dynamic-token"})

    @app.route("/protected")
    def protected():
        auth = request.headers.get("Authorization", "")
        if auth != "Bearer dynamic-token":
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({"result": "ok"})

    base_url = app_runner.openapi_url(app, path="")
    assert (
        cli.run(
            f"{base_url}/openapi.json",
            "--include-path=/protected",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 3",
            config={
                "base-url": base_url,
                "auth": _dynamic_auth("BearerAuth"),
            },
        )
        == snapshot_cli
    )


def test_dynamic_auth_wsgi_e2e(testdir):
    testdir.makefile(
        ".toml",
        schemathesis="""
[auth.dynamic.openapi.BearerAuth]
path = "/api/auth"
extract_selector = "/access_token"
""",
    )
    testdir.makepyfile(
        """
import schemathesis
from flask import Flask, jsonify, request
from hypothesis import Phase, settings

app = Flask("test")

@app.route("/openapi.json")
def spec():
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0"},
        "paths": {
            "/protected": {
                "get": {
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {"description": "OK"}}
                }
            }
        },
        "components": {
            "securitySchemes": {
                "BearerAuth": {"type": "http", "scheme": "bearer"}
            }
        },
    }

@app.route("/api/auth", methods=["POST"])
def auth():
    return jsonify({"access_token": "secret-token"})

@app.route("/protected")
def protected():
    auth_header = request.headers.get("Authorization", "")
    if auth_header == "Bearer secret-token":
        return jsonify({"result": "ok"})
    return jsonify({"error": "unauthorized"}), 401

schema = schemathesis.openapi.from_wsgi("/openapi.json", app)

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    response = case.call()
    assert response.status_code == 200
"""
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)


def test_dynamic_auth_asgi_e2e(testdir):
    testdir.makefile(
        ".toml",
        schemathesis="""
[auth.dynamic.openapi.HTTPBearer]
path = "/api/auth"
extract_selector = "/access_token"
""",
    )
    testdir.makepyfile(
        """
import schemathesis
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from hypothesis import Phase, settings

app = FastAPI()
security = HTTPBearer(auto_error=False)

@app.post("/api/auth", include_in_schema=False)
async def auth():
    return {"access_token": "secret-token"}

@app.get("/protected")
async def protected(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials is None or credentials.credentials != "secret-token":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"result": "ok"}

schema = schemathesis.openapi.from_asgi("/openapi.json", app)

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    response = case.call()
    assert response.status_code == 200
"""
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)


def test_dynamic_auth_wsgi_transport_error(testdir):
    testdir.makefile(
        ".toml",
        schemathesis="""
[auth.dynamic.openapi.BearerAuth]
path = "/api/auth"
extract_selector = "/access_token"
""",
    )
    testdir.makepyfile(
        """
import schemathesis
from flask import Flask, jsonify
from hypothesis import Phase, settings

app = Flask("test")
app.config["TESTING"] = True

@app.route("/openapi.json")
def spec():
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0"},
        "paths": {
            "/data": {
                "get": {
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {"description": "OK"}}
                }
            }
        },
        "components": {
            "securitySchemes": {
                "BearerAuth": {"type": "http", "scheme": "bearer"}
            }
        },
    }

@app.route("/api/auth", methods=["POST"])
def auth():
    raise RuntimeError("auth endpoint crashed")

@app.route("/data")
def data():
    return jsonify({"result": "ok"})

schema = schemathesis.openapi.from_wsgi("/openapi.json", app)

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    case.call()
"""
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*WSGI auth request failed*"])


def test_dynamic_auth_asgi_transport_error(testdir):
    testdir.makefile(
        ".toml",
        schemathesis="""
[auth.dynamic.openapi.HTTPBearer]
path = "/api/auth"
extract_selector = "/access_token"
""",
    )
    testdir.makepyfile(
        """
import schemathesis
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from hypothesis import Phase, settings

app = FastAPI()
security = HTTPBearer(auto_error=False)

@app.post("/api/auth", include_in_schema=False)
async def auth():
    raise RuntimeError("auth endpoint crashed")

@app.get("/protected")
async def protected(credentials: HTTPAuthorizationCredentials = Depends(security)):
    return {"result": "ok"}

schema = schemathesis.openapi.from_asgi("/openapi.json", app)

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    case.call()
"""
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*ASGI auth request failed*"])


def test_dynamic_auth_integration_oauth2(ctx, cli, app_runner, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/protected": {
                "get": {
                    "operationId": "getProtected",
                    "security": [{"OAuth2": []}],
                    "responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorized"}},
                }
            }
        },
        components={
            "securitySchemes": {
                "OAuth2": OAUTH2_SCHEME,
            }
        },
    )

    @app.route("/api/auth", methods=["POST"])
    def auth_endpoint():
        return jsonify({"access_token": "dynamic-token"})

    @app.route("/protected")
    def protected():
        auth = request.headers.get("Authorization", "")
        if auth != "Bearer dynamic-token":
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({"result": "ok"})

    base_url = app_runner.openapi_url(app, path="")
    assert (
        cli.run(
            f"{base_url}/openapi.json",
            "--include-path=/protected",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 3",
            config={
                "base-url": base_url,
                "auth": _dynamic_auth("OAuth2"),
            },
        )
        == snapshot_cli
    )


@pytest.mark.parametrize("cache_by_key", [None, lambda case, ctx: "k"], ids=["unkeyed", "keyed"])
def test_refresh_auth_refetches(auth_operation, cache_by_key):
    tokens = iter(["old", "new"])

    @schemathesis.auth(retry_on=[401], cache_by_key=cache_by_key)
    class Rotating:
        def get(self, case, ctx):
            return next(tokens)

        def set(self, case, data, ctx):
            case.headers = case.headers or {}
            case.headers["Authorization"] = f"Bearer {data}"

    case = auth_operation.Case()
    set_on_case(case, AuthContext(operation=auth_operation, app=None), None)
    assert case.headers["Authorization"] == "Bearer old"
    refresh_auth(case)
    assert case.headers["Authorization"] == "Bearer new"


def test_refresh_failure_keeps_original_response(auth_operation, response_factory):

    @schemathesis.auth(retry_on=[401])
    class Dead:
        def get(self, case, ctx):
            raise AuthenticationError("Dead", "get", "credentials revoked")

        def set(self, case, data, ctx):
            case.headers["Authorization"] = f"Bearer {data}"

    case = auth_operation.Case()
    case._has_explicit_auth = True
    initial = response_factory.requests(status_code=401)
    state = ReauthState(retry_on_statuses=frozenset({401}))

    def recall():
        raise AssertionError("must not replay when the refresh itself failed")

    assert reauth_and_replay(case, initial, state, recall) is initial
    assert state.reauth_count == 0
    assert state._consecutive_failures == 1


def test_replay_transport_error_keeps_initial_response(auth_operation, response_factory):
    case = auth_operation.Case()
    case._has_explicit_auth = True
    initial = response_factory.requests(status_code=401)
    state = ReauthState(retry_on_statuses=frozenset({401}))

    def recall():
        raise requests.ConnectionError("connection dropped on replay")

    assert reauth_and_replay(case, initial, state, recall) is initial
    assert state.reauth_count == 0


# A WSGI-loaded schema must refetch its token through the in-process app, not real outbound HTTP.
def test_refresh_auth_wsgi_propagates_app(ctx):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/protected": {
                "get": {
                    "operationId": "getProtected",
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "securitySchemes": {
                "BearerAuth": {"type": "http", "scheme": "bearer"},
            }
        },
    )
    tokens = itertools.count(1)

    @app.route("/api/auth", methods=["POST"])
    def auth_endpoint():
        return jsonify({"access_token": f"token-{next(tokens)}"})

    schema = schemathesis.openapi.from_wsgi("/openapi.json", app)
    schema.config.auth.dynamic.schemes["BearerAuth"] = DynamicTokenAuthConfig(
        path="/api/auth", extract_selector="/access_token"
    )
    operation = schema["/protected"]["GET"]
    case = operation.Case()
    set_on_case(case, AuthContext(operation=operation, app=operation.app), None)
    assert case.headers["Authorization"] == "Bearer token-1"

    refresh_auth(case)

    assert case.headers["Authorization"] == "Bearer token-2"


# Single-use token 401s after first use; the hook re-authenticates and replays so the recorded response is the recovered 2xx.
def test_reauth_recovers_expired_token(ctx, cli, app_runner, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/protected": {
                "get": {
                    "operationId": "getProtected",
                    "security": [{"OAuth2": []}],
                    "parameters": [{"name": "key", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorized"}},
                }
            }
        },
        components={
            "securitySchemes": {
                "OAuth2": OAUTH2_SCHEME,
            }
        },
    )
    state = _register_single_use_token(app)

    base_url = app_runner.openapi_url(app, path="")
    result = cli.run(
        f"{base_url}/openapi.json",
        "--include-path=/protected",
        "--phases=fuzzing",
        "--mode=positive",
        "-n 4",
        config={
            "base-url": base_url,
            "checks": {"positive_data_acceptance": {"expected-statuses": ["2xx"]}},
            "auth": _dynamic_auth("OAuth2", retry_on=[401]),
        },
    )
    assert result == snapshot_cli
    assert state["issued"] >= 2


# A negated-security case's 401 is the expected outcome, not an expired token - the hook must not re-authenticate for it.
def test_negative_auth_case_does_not_reauth(ctx, cli, app_runner, mocker):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/protected": {
                "get": {
                    "operationId": "getProtected",
                    "security": [{"ApiKeyAuth": []}],
                    "responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorized"}},
                }
            }
        },
        components={
            "securitySchemes": {
                "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
            }
        },
    )

    @app.route("/api/auth", methods=["POST"])
    def auth_endpoint():
        return jsonify({"access_token": "valid-token"})

    @app.route("/protected")
    def protected():
        if request.headers.get("X-API-Key", "") != "valid-token":
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({"result": "ok"})

    spy = mocker.spy(schemathesis.auths, "refresh_auth")

    base_url = app_runner.openapi_url(app, path="")
    cli.run(
        f"{base_url}/openapi.json",
        "--include-path=/protected",
        "--phases=coverage",
        "--mode=all",
        "-n 15",
        config={
            "base-url": base_url,
            "auth": _dynamic_auth("ApiKeyAuth", retry_on=[401]),
        },
    )
    assert spy.call_count == 0


def test_dynamic_auth_integration_api_key(ctx, cli, app_runner, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/protected": {
                "get": {
                    "operationId": "getProtected",
                    "security": [{"ApiKeyAuth": []}],
                    "responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorized"}},
                }
            }
        },
        components={
            "securitySchemes": {
                "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
            }
        },
    )

    @app.route("/api/auth", methods=["POST"])
    def auth_endpoint():
        return jsonify({"access_token": "dynamic-key"})

    @app.route("/protected")
    def protected():
        key = request.headers.get("X-API-Key", "")
        if key != "dynamic-key":
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({"result": "ok"})

    base_url = app_runner.openapi_url(app, path="")
    assert (
        cli.run(
            f"{base_url}/openapi.json",
            "--include-path=/protected",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 3",
            config={
                "base-url": base_url,
                "auth": _dynamic_auth("ApiKeyAuth"),
            },
        )
        == snapshot_cli
    )


# A no-auth operation's incidental 401 must not trigger a re-authentication that invalidates the shared token cache.
def test_oauth2_case_without_explicit_auth_does_not_reauth(ctx, cli, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/public": {
                "get": {
                    "operationId": "getPublic",
                    "parameters": [{"name": "q", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorized"}},
                }
            },
            "/protected": {
                "get": {
                    "operationId": "getProtected",
                    "security": [{"OAuth2": []}],
                    "parameters": [{"name": "key", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorized"}},
                }
            },
        },
        components={
            "securitySchemes": {
                "OAuth2": OAUTH2_SCHEME,
            }
        },
    )
    hits = {"auth": 0}

    @app.route("/api/auth", methods=["POST"])
    def auth_endpoint():
        hits["auth"] += 1
        return jsonify({"access_token": "t"})

    @app.route("/public")
    def public():
        return jsonify({"error": "unauthorized"}), 401

    @app.route("/protected")
    def protected():
        if request.headers.get("Authorization") == "Bearer t":
            return jsonify({"result": "ok"})
        return jsonify({"error": "unauthorized"}), 401

    base_url = app_runner.openapi_url(app, path="")
    cli.run(
        f"{base_url}/openapi.json",
        "--phases=coverage",
        "--mode=all",
        "-n 15",
        config={
            "base-url": base_url,
            "checks": {
                "negative_data_rejection": {"enabled": False},
                "positive_data_acceptance": {"enabled": False},
            },
            "auth": _dynamic_auth("OAuth2", retry_on=[401]),
        },
    )
    assert hits["auth"] <= 2


# Dead credentials make every re-authentication fail; the breaker trips after 3 consecutive failures, bounding auth fetches.
def test_breaker_bounds_login_attempts(ctx, cli, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            f"/r{i}": {
                "get": {
                    "operationId": f"g{i}",
                    "security": [{"OAuth2": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
            for i in range(6)
        },
        components={
            "securitySchemes": {
                "OAuth2": OAUTH2_SCHEME,
            }
        },
    )
    hits = {"auth": 0}

    @app.route("/api/auth", methods=["POST"])
    def auth_endpoint():
        hits["auth"] += 1
        return jsonify({"access_token": "bad"})

    def _unauthorized():
        return jsonify({"error": "unauthorized"}), 401

    for i in range(6):
        app.add_url_rule(f"/r{i}", f"r{i}", _unauthorized)

    base_url = app_runner.openapi_url(app, path="")
    result = cli.run(
        f"{base_url}/openapi.json",
        "--phases=fuzzing",
        "--mode=positive",
        "-n 1",
        config={
            "base-url": base_url,
            "auth": _dynamic_auth("OAuth2", retry_on=[401]),
        },
    )
    assert hits["auth"] <= 4
    assert result.stdout.count("⚠️ Authentication stopped working mid-run - credentials likely invalidated") == 1


# A dead login endpoint is hit a bounded number of times across the whole run, not once per generated case.
def test_dead_login_endpoint_does_not_storm(ctx, cli, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/protected": {
                "get": {
                    "operationId": "getProtected",
                    "security": [{"OAuth2": []}],
                    "parameters": [{"name": "key", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorized"}},
                }
            }
        },
        components={
            "securitySchemes": {
                "OAuth2": OAUTH2_SCHEME,
            }
        },
    )
    hits = {"auth": 0}

    @app.route("/api/auth", methods=["POST"])
    def auth_endpoint():
        hits["auth"] += 1
        return jsonify({"error": "locked"}), 401

    @app.route("/protected")
    def protected():
        return jsonify({"result": "ok"})

    base_url = app_runner.openapi_url(app, path="")
    cli.run(
        f"{base_url}/openapi.json",
        "--include-path=/protected",
        "--phases=fuzzing",
        "--mode=positive",
        "-n 20",
        config={
            "base-url": base_url,
            "auth": _dynamic_auth("OAuth2", retry_on=[401]),
        },
    )
    assert hits["auth"] <= TOKEN_FETCH_BREAKER_THRESHOLD


# A programmatic @schemathesis.auth provider with retry_on re-authenticates like a config-dynamic scheme in a full run.
def test_custom_provider_reauth_recovers_expired_token(ctx, cli, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/protected": {
                "get": {
                    "operationId": "getProtected",
                    "parameters": [{"name": "key", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorized"}},
                }
            }
        },
    )
    state = _register_single_use_token(app)

    base_url = app_runner.openapi_url(app, path="")
    module = ctx.write_pymodule(
        f"""
import requests

@schemathesis.auth(retry_on=[401])
class TokenAuth:
    def get(self, case, context):
        response = requests.post("{base_url}/api/auth")
        return response.json()["access_token"]

    def set(self, case, data, context):
        case.headers = case.headers or {{}}
        case.headers["Authorization"] = f"Bearer {{data}}"
"""
    )
    result = cli.main(
        "run",
        f"{base_url}/openapi.json",
        "--include-path=/protected",
        "--phases=fuzzing",
        "--mode=positive",
        "-n 4",
        config={
            "base-url": base_url,
            "checks": {"positive_data_acceptance": {"expected-statuses": ["2xx"]}},
        },
        hooks=module,
    )
    assert result.exit_code == 0, result.stdout
    assert state["issued"] >= 2
