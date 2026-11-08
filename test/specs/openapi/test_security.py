import json

import pytest
from flask import jsonify, request

import schemathesis
from schemathesis.config._auth import AuthConfig
from schemathesis.core.jsonschema.resolver import make_root_resolver
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis.examples import generate_many
from schemathesis.specs.openapi.adapter.security import extract_security_definitions_v3
from test.utils import run_given


def test_ref_resolving():
    http_schema = {"basic_auth": {"type": "http", "scheme": "basic"}}
    schema = {
        "openapi": "3.0.0",
        "info": {"title": "Blank API", "version": "1.0"},
        "servers": [{"url": "http://localhost/api"}],
        "paths": {"foo": {"get": {"responses": {"200": {"description": "OK"}}}}},
        "components": {"securitySchemes": {"$ref": "#/components/HTTPSchema"}, "HTTPSchema": http_schema},
    }
    resolver = make_root_resolver(schema)
    assert extract_security_definitions_v3(schema, resolver) == http_schema


def test_ref_resolving_nested():
    http_schema = {"type": "http", "scheme": "basic"}
    schema = {
        "openapi": "3.0.0",
        "info": {"title": "Blank API", "version": "1.0"},
        "servers": [{"url": "http://localhost/api"}],
        "paths": {"foo": {"get": {"responses": {"200": {"description": "OK"}}}}},
        "components": {
            "securitySchemes": {"basic_auth": {"$ref": "#/components/HTTPSchema"}},
            "HTTPSchema": http_schema,
        },
    }
    resolver = make_root_resolver(schema)
    assert extract_security_definitions_v3(schema, resolver) == {"basic_auth": http_schema}


@pytest.mark.parametrize(
    ("components", "files"),
    [
        (
            {
                "x-alias": {"A": {"$ref": "schemes/apikey.json"}},
                "securitySchemes": {"ApiKey": {"$ref": "#/components/x-alias/A"}},
            },
            {},
        ),
        (
            {"securitySchemes": {"$ref": "security.json"}},
            {"security.json": {"ApiKey": {"$ref": "schemes/apikey.json"}}},
        ),
    ],
    ids=["aliased-scheme", "external-map"],
)
def test_security_scheme_ref_chain(ctx, tmp_path, components, files):
    raw_schema = ctx.openapi.build_schema(
        {"/items": {"get": {"responses": {"200": {"description": "OK"}}}}},
        components=components,
        security=[{"ApiKey": []}],
    )
    (tmp_path / "schemes").mkdir()
    (tmp_path / "schemes" / "apikey.json").write_text(json.dumps({"type": "apiKey", "in": "header", "name": "X-Key"}))
    for name, content in files.items():
        (tmp_path / name).write_text(json.dumps(content))
    (tmp_path / "openapi.json").write_text(json.dumps(raw_schema))
    schema = schemathesis.openapi.from_path(str(tmp_path / "openapi.json"))

    @run_given(case=schema["/items"]["GET"].as_strategy(), max_examples=5)
    def test(case):
        assert "X-Key" in case.headers


PARAMETER_NAME = "TestApiKey"
SCHEMA_WITH_PARAMETER_AND_SECURITY_SCHEME = {
    "openapi": "3.0.2",
    "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
    "paths": {
        "/test": {
            "parameters": [
                {
                    "name": PARAMETER_NAME,
                    "in": "header",
                    "required": True,
                    "schema": {"enum": ["EXPECTED"]},
                }
            ],
            "get": {"responses": {"200": {"description": "OK"}}},
        }
    },
    "components": {
        "securitySchemes": {
            "ApiKeyAuth": {"type": "apiKey", "name": PARAMETER_NAME, "in": "header"},
            "BasicAuth": {"type": "http", "scheme": "basic"},
        }
    },
    "security": [{"ApiKeyAuth": []}, {"BasicAuth": []}],
}


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, "EXPECTED"),
        ({"headers": {"TestApiKey": "EXPLICIT"}}, "EXPLICIT"),
    ],
)
def test_name_clash(ctx, kwargs, expected):
    # Operation definition should take precedence over security schemes
    # Explicit headers should take precedence over everything else
    schema = ctx.openapi.load_schema(
        SCHEMA_WITH_PARAMETER_AND_SECURITY_SCHEME["paths"],
        components=SCHEMA_WITH_PARAMETER_AND_SECURITY_SCHEME["components"],
        security=SCHEMA_WITH_PARAMETER_AND_SECURITY_SCHEME["security"],
    )

    @run_given(case=schema["/test"]["GET"].as_strategy(**kwargs), max_examples=100)
    def test(case):
        assert case.headers[PARAMETER_NAME] == expected


@pytest.mark.parametrize("with_security_parameters", [True, False])
def test_without_security_parameters(ctx, with_security_parameters):
    schema = ctx.openapi.load_schema(
        {"/test": {"get": {"responses": {"200": {"description": "OK"}}}}},
        version="3.0.0",
        servers=[{"url": "http://localhost/api"}],
        components={
            "securitySchemes": {"basic_auth": {"type": "http", "scheme": "basic"}},
        },
        security=[{"basic_auth": []}],
    )
    schema.config.generation.update(with_security_parameters=with_security_parameters)

    @run_given(case=schema["/test"]["GET"].as_strategy(), max_examples=20)
    def test(case):
        if with_security_parameters:
            assert "Authorization" in case.headers
        else:
            assert case.headers == {}


@pytest.mark.parametrize("version", ["2.0", "3.0.2"])
def test_undefined_security_scheme_is_ignored(ctx, version):
    # When a security requirement references a scheme that is NOT defined in securitySchemes/securityDefinitions
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "security": [{"undefined_scheme": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version=version,
    )
    operation = schema["/users"]["get"]
    # Then it should be ignored
    assert not list(operation.security.iter_parameters())


def test_invalid_security_requirement_types_are_ignored(ctx):
    # When security requirements contain non-dict elements
    operation = ctx.openapi.load_operation(
        "GET /test",
        {
            "security": [
                "invalid_string",
                123,
                ["invalid", "list"],
                None,
                # Valid: should be processed
                {"ApiKeyAuth": []},
            ],
            "responses": {"200": {"description": "OK"}},
        },
        components={
            "securitySchemes": {
                "ApiKeyAuth": {"type": "apiKey", "name": "X-API-Key", "in": "header"},
            }
        },
    )

    # Should not crash and should extract parameters from the valid requirement
    params = list(operation.security.iter_parameters())
    # Only the valid ApiKeyAuth requirement should be processed
    assert len(params) == 1
    assert params[0].name == "X-API-Key"
    assert params[0].location == "header"


SECURITY_DEFINITIONS = {
    "2.0": {
        "securityDefinitions": {
            "basicAuth": {"type": "basic"},
            "apiKeyAuth": {"type": "apiKey", "name": "X-API-Key", "in": "header"},
        }
    },
    "3.0.2": {
        "components": {
            "securitySchemes": {
                "basicAuth": {"type": "http", "scheme": "basic"},
                "apiKeyAuth": {"type": "apiKey", "name": "X-API-Key", "in": "header"},
            }
        }
    },
}


SECURITY_CREDENTIAL_CASES = [
    ([{"basicAuth": []}, {"apiKeyAuth": []}], {"Authorization", "X-API-Key"}),
    ([{"basicAuth": []}, {"apiKeyAuth": []}, {}], set()),
]
CREDENTIAL_HEADERS = {"Authorization", "X-API-Key"}


def _schema_with_security(ctx, version, security):
    return ctx.openapi.load_schema(
        {"/test": {"get": {"responses": {"200": {"description": "OK"}}}}},
        version=version,
        security=security,
        **SECURITY_DEFINITIONS[version],
    )


@pytest.mark.parametrize("version", ["2.0", "3.0.2"])
@pytest.mark.parametrize(("security", "expected"), SECURITY_CREDENTIAL_CASES, ids=["required", "optional"])
def test_generated_credentials_in_coverage(ctx, version, security, expected):
    schema = _schema_with_security(ctx, version, security)
    operation = schema["/test"]["GET"]
    cases = schema.iter_coverage_cases(
        operation,
        generation_modes=[GenerationMode.POSITIVE, GenerationMode.NEGATIVE],
        generation_config=schema.config.generation_for(operation=operation),
    )
    assert {name for case in cases for name in case.headers} & CREDENTIAL_HEADERS == expected


@pytest.mark.parametrize("version", ["2.0", "3.0.2"])
@pytest.mark.parametrize(("security", "expected"), SECURITY_CREDENTIAL_CASES, ids=["required", "optional"])
def test_generated_credentials_in_fuzzing(ctx, version, security, expected):
    schema = _schema_with_security(ctx, version, security)
    seen = set()

    @run_given(case=schema["/test"]["GET"].as_strategy(), max_examples=10)
    def test(case):
        seen.update(case.headers)

    assert seen == expected


def test_optional_auth_keeps_configured_credentials(ctx):
    schema = _schema_with_security(ctx, "3.0.2", [{"basicAuth": []}, {"apiKeyAuth": []}, {}])
    schema.config.auth = AuthConfig.from_dict({"openapi": {"basicAuth": {"username": "user", "password": "secret"}}})

    @run_given(case=schema["/test"]["GET"].as_strategy(), max_examples=5)
    def test(case):
        assert case.headers == {"Authorization": "Basic dXNlcjpzZWNyZXQ="}


@pytest.mark.parametrize(
    "auth",
    [
        {"openapi": {"basicAuth": {"username": "user", "password": "secret"}}},
        {"basic": {"username": "user", "password": "secret"}},
    ],
    ids=["openapi", "basic"],
)
def test_configured_auth_skipped_when_negated_alongside_body(ctx, auth):
    operation = ctx.openapi.load_operation(
        "POST /test",
        {
            "security": [{"basicAuth": []}],
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"type": "object", "required": ["name"]}}},
            },
            "responses": {"200": {"description": "OK"}},
        },
        components={"securitySchemes": {"basicAuth": {"type": "http", "scheme": "basic"}}},
    )
    operation.schema.config.auth = AuthConfig.from_dict(auth)
    negated_with_body = [
        case
        for case in generate_many(operation.as_strategy(generation_mode=GenerationMode.NEGATIVE), 50)
        if {mutation.parameter for mutation in case.meta.phase.data.mutations} == {"Authorization", None}
    ]
    assert negated_with_body
    assert "Basic dXNlcjpzZWNyZXQ=" not in [case.headers.get("Authorization") for case in negated_with_body]


SECURITY_REQUIRED_CASES = [
    ([{"basicAuth": []}], {"Authorization": True}),
    ([{"basicAuth": [], "apiKeyAuth": []}], {"Authorization": True, "X-API-Key": True}),
    ([{"basicAuth": []}, {"apiKeyAuth": []}], {"Authorization": False, "X-API-Key": False}),
    ([{"basicAuth": []}, {"basicAuth": [], "apiKeyAuth": []}], {"Authorization": True, "X-API-Key": False}),
]


@pytest.mark.parametrize("version", ["2.0", "3.0.2"])
@pytest.mark.parametrize(
    ("security", "expected"),
    SECURITY_REQUIRED_CASES,
    ids=["single", "combined", "alternatives", "shared-and-alternative"],
)
def test_only_schemes_demanded_by_every_alternative_are_required(ctx, version, security, expected):
    operation = _schema_with_security(ctx, version, security)["/test"]["GET"]
    assert {
        parameter.name: parameter.definition["required"] for parameter in operation.security.iter_parameters()
    } == expected


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
