import pytest
from flask import Flask, jsonify, request

AUTH_CONFIGS = {
    "ApiKeyHeader": {"api_key": "valid-key"},
    "ApiKeyQuery": {"api_key": "valid-key"},
    "ApiKeyCookie": {"api_key": "valid-session"},
    "BasicAuth": {"username": "testuser", "password": "testpass"},
    "BearerAuth": {"bearer": "valid-token"},
}


def create_auth_test_app(ctx):
    app = Flask(__name__)

    spec = ctx.openapi.build_schema(
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

    @app.route("/openapi.json")
    def openapi_spec():
        return jsonify(spec)

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
def auth_app_port(ctx, app_runner):
    app = create_auth_test_app(ctx)
    return app_runner.run_flask_app(app)


@pytest.mark.parametrize(
    "config_section,endpoint",
    [
        ("ApiKeyHeader", "/api-key-header"),
        ("ApiKeyQuery", "/api-key-query"),
        ("ApiKeyCookie", "/api-key-cookie"),
        ("BasicAuth", "/basic-auth"),
        ("BearerAuth", "/bearer-auth"),
    ],
)
def test_openapi_auth_schemes(cli, auth_app_port, snapshot_cli, config_section, endpoint):
    assert (
        cli.run(
            f"http://127.0.0.1:{auth_app_port}/openapi.json",
            f"--include-path={endpoint}",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
            config={"auth": {"openapi": {config_section: AUTH_CONFIGS[config_section]}}},
        )
        == snapshot_cli
    )


def test_multiple_auth_and_semantics(cli, auth_app_port, snapshot_cli):
    assert (
        cli.run(
            f"http://127.0.0.1:{auth_app_port}/openapi.json",
            "--include-path=/multiple-auth-and",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
            config={
                "auth": {
                    "openapi": {
                        "ApiKeyHeader": {"api_key": "valid-key"},
                        "BearerAuth": {"bearer": "valid-token"},
                    }
                }
            },
        )
        == snapshot_cli
    )


def test_multiple_auth_or_semantics(cli, auth_app_port, snapshot_cli):
    assert (
        cli.run(
            f"http://127.0.0.1:{auth_app_port}/openapi.json",
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


def test_optional_auth(cli, auth_app_port, snapshot_cli):
    assert (
        cli.run(
            f"http://127.0.0.1:{auth_app_port}/openapi.json",
            "--include-path=/optional-auth",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
        )
        == snapshot_cli
    )


def test_fallback_to_cli_auth(cli, auth_app_port, snapshot_cli):
    assert (
        cli.run(
            f"http://127.0.0.1:{auth_app_port}/openapi.json",
            "--auth=testuser:testpass",
            "--include-path=/basic-auth",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize("invalid_scheme", ["NonExistentAuth", "ApiKeyHeadr"])
def test_unused_openapi_auth_warnings(cli, auth_app_port, snapshot_cli, invalid_scheme):
    assert (
        cli.run(
            f"http://127.0.0.1:{auth_app_port}/openapi.json",
            "--include-path=/api-key-header",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
            config={"auth": {"openapi": {invalid_scheme: {"api_key": "test"}}}},
        )
        == snapshot_cli
    )


def test_openapi_v2_swagger(ctx, cli, app_runner, snapshot_cli):
    app = Flask(__name__)

    spec = ctx.openapi.build_schema(
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

    port = app_runner.run_flask_app(app)

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/swagger.json",
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


def test_cli_auth_precedence_over_openapi(cli, auth_app_port, snapshot_cli):
    assert (
        cli.run(
            f"http://127.0.0.1:{auth_app_port}/openapi.json",
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


def test_global_security_overridden_by_operation(ctx, cli, app_runner, snapshot_cli):
    app = Flask(__name__)

    spec = ctx.openapi.build_schema(
        {
            "/operation-override": {
                "get": {
                    "operationId": "operation_override",
                    "security": [{"OperationAuth": []}],  # Override with operation-specific
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
        components={
            "securitySchemes": {
                "GlobalAuth": {"type": "apiKey", "name": "X-Global", "in": "header"},
                "OperationAuth": {"type": "apiKey", "name": "X-Operation", "in": "header"},
            }
        },
        security=[{"GlobalAuth": []}],  # Global default
    )

    @app.route("/openapi.json")
    def openapi_spec():
        return jsonify(spec)

    @app.route("/operation-override")
    def operation_override():
        # Should receive OperationAuth, not GlobalAuth
        if request.headers.get("X-Operation") == "operation-key":
            return jsonify({"status": "authenticated"})
        return jsonify({"error": "unauthorized"}), 401

    port = app_runner.run_flask_app(app)

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
            config={
                "auth": {
                    "openapi": {
                        "GlobalAuth": {"api_key": "global-key"},
                        "OperationAuth": {"api_key": "operation-key"},
                    }
                }
            },
        )
        == snapshot_cli
    )


def test_partial_scheme_configuration(cli, auth_app_port, snapshot_cli):
    assert (
        cli.run(
            f"http://127.0.0.1:{auth_app_port}/openapi.json",
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


def test_no_security_requirements(cli, app_runner, snapshot_cli):
    app = Flask(__name__)

    @app.route("/openapi.json")
    def openapi_spec():
        return jsonify(
            {
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
        )

    @app.route("/public")
    def public_endpoint():
        return jsonify({"status": "ok"})

    port = app_runner.run_flask_app(app)

    # Auth is configured but should not be applied since no security requirements
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
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


def test_multiple_or_requirements_first_match(ctx, cli, app_runner, snapshot_cli):
    app = Flask(__name__)

    spec = ctx.openapi.build_schema(
        {
            "/multi-or": {
                "get": {
                    "operationId": "multi_or",
                    "security": [
                        {"FirstAuth": []},
                        {"SecondAuth": []},
                        {"ThirdAuth": []},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
        components={
            "securitySchemes": {
                "FirstAuth": {"type": "apiKey", "name": "X-First", "in": "header"},
                "SecondAuth": {"type": "apiKey", "name": "X-Second", "in": "header"},
                "ThirdAuth": {"type": "apiKey", "name": "X-Third", "in": "header"},
            }
        },
    )

    @app.route("/openapi.json")
    def openapi_spec():
        return jsonify(spec)

    @app.route("/multi-or")
    def multi_or():
        # Accept any of the three auth methods
        if (
            request.headers.get("X-First") == "first-key"
            or request.headers.get("X-Second") == "second-key"
            or request.headers.get("X-Third") == "third-key"
        ):
            return jsonify({"status": "authenticated"})
        return jsonify({"error": "unauthorized"}), 401

    port = app_runner.run_flask_app(app)

    # Only configure SecondAuth - should use it even though FirstAuth comes first in schema
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
            config={
                "auth": {
                    "openapi": {
                        "SecondAuth": {"api_key": "second-key"},
                    }
                }
            },
        )
        == snapshot_cli
    )


def test_auth_with_invalid_scheme_in_schema(ctx, cli, app_runner, snapshot_cli):
    app = Flask(__name__)

    spec = ctx.openapi.build_schema(
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

    @app.route("/openapi.json")
    def openapi_spec():
        return jsonify(spec)

    @app.route("/protected")
    def protected():
        return jsonify({"error": "unauthorized"}), 401

    port = app_runner.run_flask_app(app)

    # OAuth2 scheme exists but isn't supported - should fall back to CLI auth if provided
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
        )
        == snapshot_cli
    )


def test_referenced_security_scheme(ctx, cli, app_runner, snapshot_cli):
    app = Flask(__name__)

    spec = ctx.openapi.build_schema(
        {
            "/api-key": {
                "get": {
                    "security": [{"ApiKeyAlias": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
        components={
            "securitySchemes": {
                "ActualApiKey": {"type": "apiKey", "name": "X-API-Key", "in": "header"},
                "ApiKeyAlias": {"$ref": "#/components/securitySchemes/ActualApiKey"},
            }
        },
    )

    @app.route("/openapi.json")
    def openapi_spec():
        return jsonify(spec)

    @app.route("/api-key")
    def api_key():
        key = request.headers.get("X-API-Key", "")
        if key == "valid-key":
            return jsonify({"status": "authenticated"})
        return jsonify({"error": "unauthorized"}), 401

    port = app_runner.run_flask_app(app)

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--include-path=/api-key",
            "--phases=fuzzing",
            "--mode=positive",
            "-n 5",
            config={
                "auth": {
                    "openapi": {
                        "ApiKeyAlias": {"api_key": "valid-key"},
                    }
                }
            },
        )
        == snapshot_cli
    )
