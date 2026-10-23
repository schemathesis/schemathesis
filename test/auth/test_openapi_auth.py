import pytest
from flask import jsonify, request

AUTH_CONFIGS = {
    "ApiKeyHeader": {"api_key": "valid-key"},
    "ApiKeyQuery": {"api_key": "valid-key"},
    "ApiKeyCookie": {"api_key": "valid-session"},
    "BasicAuth": {"username": "testuser", "password": "testpass"},
    "BearerAuth": {"bearer": "valid-token"},
}


def create_auth_test_app(ctx):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api-key-header": {
                "get": {
                    "operationId": "api_key_header",
                    "security": [{"ApiKeyHeader": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/api-key-query": {
                "get": {
                    "operationId": "api_key_query",
                    "security": [{"ApiKeyQuery": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/api-key-cookie": {
                "get": {
                    "operationId": "api_key_cookie",
                    "security": [{"ApiKeyCookie": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/basic-auth": {
                "get": {
                    "operationId": "basic_auth",
                    "security": [{"BasicAuth": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/bearer-auth": {
                "get": {
                    "operationId": "bearer_auth",
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/multiple-auth-and": {
                "get": {
                    "operationId": "multiple_auth_and",
                    "security": [{"ApiKeyHeader": [], "BearerAuth": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/multiple-auth-or": {
                "get": {
                    "operationId": "multiple_auth_or",
                    "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/optional-auth": {
                "get": {
                    "operationId": "optional_auth",
                    "security": [{"ApiKeyHeader": []}, {}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
        version="3.0.0",
        components={
            "securitySchemes": {
                "ApiKeyHeader": {"type": "apiKey", "name": "X-API-Key", "in": "header"},
                "ApiKeyQuery": {"type": "apiKey", "name": "api_key", "in": "query"},
                "ApiKeyCookie": {"type": "apiKey", "name": "session", "in": "cookie"},
                "BasicAuth": {"type": "http", "scheme": "basic"},
                "BearerAuth": {"type": "http", "scheme": "bearer"},
            }
        },
    )

    @app.route("/api-key-header")
    def api_key_header():
        api_key = request.headers.get("X-API-Key")
        if api_key == "valid-key":
            return jsonify({"status": "authenticated"})
        return jsonify({"error": "unauthorized"}), 401

    @app.route("/api-key-query")
    def api_key_query():
        api_key = request.args.get("api_key")
        if api_key == "valid-key":
            return jsonify({"status": "authenticated"})
        return jsonify({"error": "unauthorized"}), 401

    @app.route("/api-key-cookie")
    def api_key_cookie():
        session = request.cookies.get("session")
        if session == "valid-session":
            return jsonify({"status": "authenticated"})
        return jsonify({"error": "unauthorized"}), 401

    @app.route("/basic-auth")
    def basic_auth():
        auth = request.authorization
        if auth and auth.username == "testuser" and auth.password == "testpass":
            return jsonify({"status": "authenticated"})
        return jsonify({"error": "unauthorized"}), 401

    @app.route("/bearer-auth")
    def bearer_auth():
        auth = request.headers.get("Authorization")
        if auth == "Bearer valid-token":
            return jsonify({"status": "authenticated"})
        return jsonify({"error": "unauthorized"}), 401

    @app.route("/multiple-auth-and")
    def multiple_auth_and():
        api_key = request.headers.get("X-API-Key")
        bearer = request.headers.get("Authorization")
        if api_key == "valid-key" and bearer == "Bearer valid-token":
            return jsonify({"status": "authenticated"})
        return jsonify({"error": "unauthorized"}), 401

    @app.route("/multiple-auth-or")
    def multiple_auth_or():
        api_key = request.headers.get("X-API-Key")
        bearer = request.headers.get("Authorization")
        if api_key == "valid-key" or bearer == "Bearer valid-token":
            return jsonify({"status": "authenticated"})
        return jsonify({"error": "unauthorized"}), 401

    @app.route("/optional-auth")
    def optional_auth():
        return jsonify({"status": "ok"})

    return app


@pytest.fixture
def auth_schema_url(ctx, cli, app_runner):
    app = create_auth_test_app(ctx)
    return app_runner.openapi_url(app)


def test_openapi_auth_schemes(cli, auth_schema_url, snapshot_cli):
    assert (
        cli.run(
            auth_schema_url,
            "--include-path-regex=^/(api-key-|basic-auth|bearer-auth|multiple-auth-and)",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
            config={"auth": {"openapi": AUTH_CONFIGS}},
        )
        == snapshot_cli
    )


def test_multiple_auth_or_semantics(cli, auth_schema_url, snapshot_cli):
    assert (
        cli.run(
            auth_schema_url,
            "--include-path=/multiple-auth-or",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
            config={
                "auth": {
                    "openapi": {
                        "ApiKeyHeader": {"api_key": "valid-key"},
                    }
                }
            },
        )
        == snapshot_cli
    )


def test_optional_auth(cli, auth_schema_url, snapshot_cli):
    assert (
        cli.run(
            auth_schema_url,
            "--include-path=/optional-auth",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
        )
        == snapshot_cli
    )


def test_fallback_to_cli_auth(cli, auth_schema_url, snapshot_cli):
    assert (
        cli.run(
            auth_schema_url,
            "--auth=testuser:testpass",
            "--include-path=/basic-auth",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize("invalid_scheme", ["NonExistentAuth", "ApiKeyHeadr"])
def test_unused_openapi_auth_warnings(cli, auth_schema_url, snapshot_cli, invalid_scheme):
    assert (
        cli.run(
            auth_schema_url,
            "--include-path=/api-key-header",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
            config={"auth": {"openapi": {invalid_scheme: {"api_key": "test"}}}},
        )
        == snapshot_cli
    )


def test_openapi_v2_swagger(ctx, cli, app_runner, snapshot_cli):
    app, spec = ctx.openapi.make_flask_app(
        {
            "/api-key": {
                "get": {
                    "operationId": "api_key",
                    "security": [{"ApiKeyAuth": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/basic": {
                "get": {
                    "operationId": "basic",
                    "security": [{"BasicAuth": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
        version="2.0",
        basePath="/",
        securityDefinitions={
            "ApiKeyAuth": {"type": "apiKey", "name": "X-API-Key", "in": "header"},
            "BasicAuth": {"type": "basic"},
        },
    )

    @app.route("/swagger.json")
    def swagger_spec():
        return jsonify(spec)

    @app.route("/api-key")
    def api_key():
        api_key = request.headers.get("X-API-Key")
        if api_key == "valid-key":
            return jsonify({"status": "authenticated"})
        return jsonify({"error": "unauthorized"}), 401

    @app.route("/basic")
    def basic():
        auth = request.authorization
        if auth and auth.username == "user" and auth.password == "pass":
            return jsonify({"status": "authenticated"})
        return jsonify({"error": "unauthorized"}), 401

    schema_url = app_runner.openapi_url(app, path="/swagger.json")

    assert (
        cli.run(
            schema_url,
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
            config={
                "auth": {
                    "openapi": {
                        "ApiKeyAuth": {"api_key": "valid-key"},
                        "BasicAuth": {"username": "user", "password": "pass"},
                    }
                }
            },
        )
        == snapshot_cli
    )


def test_cli_auth_precedence_over_openapi(cli, auth_schema_url, snapshot_cli):
    assert (
        cli.run(
            auth_schema_url,
            "--auth=wrong:credentials",
            "--include-path=/basic-auth",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
            config={
                "auth": {
                    "openapi": {
                        "BasicAuth": {"username": "testuser", "password": "testpass"},
                    }
                }
            },
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    ("operation_security", "schemes", "extra", "accepted", "auth"),
    [
        pytest.param(
            [{"OperationAuth": []}],
            {
                "GlobalAuth": {"type": "apiKey", "name": "X-Global", "in": "header"},
                "OperationAuth": {"type": "apiKey", "name": "X-Operation", "in": "header"},
            },
            {"security": [{"GlobalAuth": []}]},
            {"X-Operation": "operation-key"},
            {"GlobalAuth": {"api_key": "global-key"}, "OperationAuth": {"api_key": "operation-key"}},
            id="global-overridden-by-operation",
        ),
        pytest.param(
            [{"FirstAuth": []}, {"SecondAuth": []}, {"ThirdAuth": []}],
            {
                "FirstAuth": {"type": "apiKey", "name": "X-First", "in": "header"},
                "SecondAuth": {"type": "apiKey", "name": "X-Second", "in": "header"},
                "ThirdAuth": {"type": "apiKey", "name": "X-Third", "in": "header"},
            },
            {},
            {"X-First": "first-key", "X-Second": "second-key", "X-Third": "third-key"},
            # Only the second alternative is configured, so it is used although the first one comes first.
            {"SecondAuth": {"api_key": "second-key"}},
            id="multiple-or-requirements",
        ),
        pytest.param(
            [{"ApiKeyAlias": []}],
            {
                "ActualApiKey": {"type": "apiKey", "name": "X-API-Key", "in": "header"},
                "ApiKeyAlias": {"$ref": "#/components/securitySchemes/ActualApiKey"},
            },
            {},
            {"X-API-Key": "valid-key"},
            {"ApiKeyAlias": {"api_key": "valid-key"}},
            id="referenced-scheme",
        ),
    ],
)
def test_operation_security_uses_configured_scheme(
    ctx, cli, snapshot_cli, operation_security, schemes, extra, accepted, auth
):
    app, _ = ctx.openapi.make_flask_app(
        {"/items": {"get": {"security": operation_security, "responses": {"200": {"description": "OK"}}}}},
        components={"securitySchemes": schemes},
        **extra,
    )

    @app.route("/items")
    def items():
        if any(request.headers.get(name) == value for name, value in accepted.items()):
            return jsonify({"status": "authenticated"})
        return jsonify({"error": "unauthorized"}), 401

    assert (
        cli.run_openapi_app(app, "--phases=fuzzing", "--mode=positive", "-n 5", config={"auth": {"openapi": auth}})
        == snapshot_cli
    )


def test_partial_scheme_configuration(cli, auth_schema_url, snapshot_cli):
    assert (
        cli.run(
            auth_schema_url,
            "--include-path=/multiple-auth-and",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
            config={
                "auth": {
                    "openapi": {
                        "ApiKeyHeader": {"api_key": "valid-key"},
                        # Missing BearerAuth
                    }
                }
            },
        )
        == snapshot_cli
    )


def test_no_security_requirements(ctx, cli, snapshot_cli):
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "No Security API", "version": "1.0.0"},
        "servers": [{"url": "/"}],
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {"type": "apiKey", "name": "X-API-Key", "in": "header"},
            }
        },
        # No global security
        "paths": {
            "/public": {
                "get": {
                    "operationId": "public_endpoint",
                    # No security requirement
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    }

    app = ctx.openapi.make_flask_app_from_schema(raw_schema)

    @app.route("/public")
    def public_endpoint():
        return jsonify({"status": "ok"})

    # Auth is configured but should not be applied since no security requirements
    assert (
        cli.run_openapi_app(
            app,
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
            config={
                "auth": {
                    "openapi": {
                        "ApiKeyAuth": {"api_key": "unused-key"},
                    }
                }
            },
        )
        == snapshot_cli
    )


def test_auth_with_invalid_scheme_in_schema(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/protected": {
                "get": {
                    "operationId": "protected",
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
        components={
            "securitySchemes": {
                # OAuth2 is not currently supported
                "OAuth2": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": "https://example.com/oauth/authorize",
                            "tokenUrl": "https://example.com/oauth/token",
                            "scopes": {"read": "Read access"},
                        }
                    },
                },
            }
        },
        security=[{"OAuth2": ["read"]}],
    )

    @app.route("/protected")
    def protected():
        return jsonify({"error": "unauthorized"}), 401

    # OAuth2 scheme exists but isn't supported - should fall back to CLI auth if provided
    assert (
        cli.run_openapi_app(
            app,
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_auth_hook_header_keeps_positive_cases_positive(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "parameters": [{"name": "authorization", "in": "header", "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/items")
    def items():
        return jsonify({"error": "rejected"}), 400

    module = ctx.write_pymodule(
        """
@schemathesis.auth()
class TokenAuth:
    def get(self, case, context):
        return "valid-token"

    def set(self, case, data, context):
        case.headers = case.headers or {}
        case.headers["Authorization"] = f"Bearer {data}"
"""
    )
    assert (
        cli.run_openapi_app(
            app,
            "--phases=fuzzing",
            "--mode=positive",
            "--max-examples=5",
            "--checks=positive_data_acceptance",
            hooks=module,
        )
        == snapshot_cli
    )


def _api_key_or_bearer_app(ctx):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "security": [{"ApiKey": []}, {"Bearer": []}],
                    "responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorized"}},
                }
            }
        },
        components={
            "securitySchemes": {
                "ApiKey": {"type": "apiKey", "name": "X-Api-Key", "in": "header"},
                "Bearer": {"type": "http", "scheme": "bearer"},
            }
        },
    )
    api_keys = []

    @app.route("/items")
    def items():
        api_keys.append(request.headers.get("X-Api-Key"))
        if "X-Api-Key" in request.headers or request.headers.get("Authorization") != "Bearer valid-token":
            return jsonify({"error": "unauthorized"}), 401
        return jsonify([])

    return app, api_keys


def test_no_alternative_credentials_with_auth_hook(ctx, cli):
    app, api_keys = _api_key_or_bearer_app(ctx)
    module = ctx.write_pymodule(
        """
@schemathesis.auth()
class TokenAuth:
    def get(self, case, context):
        return "valid-token"

    def set(self, case, data, context):
        case.headers = case.headers or {}
        case.headers["Authorization"] = f"Bearer {data}"
"""
    )
    cli.run_openapi_app(
        app,
        "--phases=coverage,fuzzing",
        "--mode=positive",
        "--max-examples=10",
        "--exclude-checks=ignored_auth",
        hooks=module,
    )
    assert api_keys
    assert set(api_keys) == {None}


def test_no_alternative_credentials_with_openapi_auth_config(ctx, cli):
    app, api_keys = _api_key_or_bearer_app(ctx)
    cli.run_openapi_app(
        app,
        "--phases=coverage,fuzzing",
        "--mode=positive",
        "--max-examples=10",
        "--exclude-checks=ignored_auth",
        config={"auth": {"openapi": {"Bearer": {"bearer": "valid-token"}}}},
    )
    assert api_keys
    assert set(api_keys) == {None}


def test_alternative_credentials_generated_without_auth(ctx, cli):
    app, api_keys = _api_key_or_bearer_app(ctx)
    cli.run_openapi_app(app, "--phases=coverage,fuzzing", "--mode=positive", "--max-examples=10")
    assert set(api_keys) - {None}
