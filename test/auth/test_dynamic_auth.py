from __future__ import annotations

import socket

import pytest
from flask import Response as FlaskResponse
from flask import jsonify, request

import schemathesis.openapi
from schemathesis.auths import AuthContext
from schemathesis.config._auth import AuthConfig, DynamicTokenAuthConfig
from schemathesis.config._error import ConfigError
from schemathesis.core.errors import AuthenticationError
from schemathesis.specs.openapi.adapter.security import build_auth_provider
from schemathesis.specs.openapi.auths import (
    ApiKeyAuthProvider,
    DynamicTokenAuthProvider,
    HttpBearerAuthProvider,
)


@pytest.fixture
def auth_operation(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/data": {
                "get": {
                    "operationId": "getData",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/api/body-auth", methods=["POST"])
    def body_auth():
        return jsonify({"access_token": "test-token"})

    @app.route("/api/nested-auth", methods=["POST"])
    def nested_auth():
        return jsonify({"data": {"token": "test-token"}})

    @app.route("/api/header-auth", methods=["POST"])
    def header_auth():
        return jsonify({}), 200, {"X-Auth-Token": "test-token"}

    @app.route("/api/fail", methods=["POST"])
    def fail():
        return jsonify({"error": "unauthorized"}), 401

    @app.route("/api/missing-key", methods=["POST"])
    def missing_key():
        return jsonify({"other": "value"})

    @app.route("/api/non-json", methods=["POST"])
    def non_json():
        return FlaskResponse("not json", content_type="text/plain")

    @app.route("/api/wrong-type", methods=["POST"])
    def wrong_type():
        return jsonify({"token": 42})

    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
    return schema["/data"]["GET"]


@pytest.mark.parametrize(
    "path,extract_from,extract_selector,expected",
    [
        ("/api/body-auth", "body", "/access_token", "test-token"),
        ("/api/nested-auth", "body", "/data/token", "test-token"),
        ("/api/header-auth", "header", "X-Auth-Token", "test-token"),
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


def test_get_raises_on_connection_error(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app({"/data": {"get": {"responses": {"200": {"description": "OK"}}}}})
    port = app_runner.run_flask_app(app)
    schema = schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/openapi.json")
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


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"path": "api/auth", "extract_selector": "/token"}, "must start with '/'"),
        (
            {"path": "/api/auth", "extract_from": "body", "extract_selector": "access_token"},
            "extract_selector.*must start with '/'",
        ),
    ],
)
def test_config_rejects_invalid_fields(kwargs, match):
    with pytest.raises(ConfigError, match=match):
        DynamicTokenAuthConfig(**kwargs)


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
        ({"type": "oauth2"}, "scheme type"),
    ],
)
def test_build_auth_provider_rejects_unsupported_scheme(scheme, match):
    config = DynamicTokenAuthConfig(path="/api/auth", extract_selector="/token")
    with pytest.raises(ConfigError, match=match):
        build_auth_provider(config, scheme)


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
    port = app_runner.run_flask_app(app)
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 1",
            config={
                "base-url": f"http://127.0.0.1:{port}",
                "auth": {
                    "dynamic": {
                        "openapi": {
                            "NonExistentAuth": {
                                "path": "/api/auth",
                                "extract_selector": "/access_token",
                            }
                        }
                    }
                },
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

    port = app_runner.run_flask_app(app)
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--include-path=/protected",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 3",
            config={
                "base-url": f"http://127.0.0.1:{port}",
                "auth": {
                    "dynamic": {
                        "openapi": {
                            "BearerAuth": {
                                "path": "/api/auth",
                                "extract_selector": "/access_token",
                            }
                        }
                    }
                },
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

    port = app_runner.run_flask_app(app)
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--include-path=/protected",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 3",
            config={
                "base-url": f"http://127.0.0.1:{port}",
                "auth": {
                    "dynamic": {
                        "openapi": {
                            "ApiKeyAuth": {
                                "path": "/api/auth",
                                "extract_selector": "/access_token",
                            }
                        }
                    }
                },
            },
        )
        == snapshot_cli
    )
