import pytest

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
