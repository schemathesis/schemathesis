import pytest
from hypothesis import HealthCheck, given, settings

import schemathesis
from schemathesis.specs.openapi.adapter.security import extract_security_definitions_v3
from schemathesis.specs.openapi.references import ReferenceResolver


def test_ref_resolving():
    http_schema = {"basic_auth": {"type": "http", "scheme": "basic"}}
    schema = {
        "openapi": "3.0.0",
        "info": {"title": "Blank API", "version": "1.0"},
        "servers": [{"url": "http://localhost/api"}],
        "paths": {"foo": {"get": {"responses": {"200": {"description": "OK"}}}}},
        "components": {"securitySchemes": {"$ref": "#/components/HTTPSchema"}, "HTTPSchema": http_schema},
    }
    resolver = ReferenceResolver("", schema)
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
    resolver = ReferenceResolver("", schema)
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
def test_name_clash(kwargs, expected):
    # Operation definition should take precedence over security schemes
    # Explicit headers should take precedence over everything else
    schema = schemathesis.openapi.from_dict(SCHEMA_WITH_PARAMETER_AND_SECURITY_SCHEME)

    @given(case=schema["/test"]["GET"].as_strategy(**kwargs))
    def test(case):
        assert case.headers[PARAMETER_NAME] == expected

    test()


@pytest.mark.parametrize("with_security_parameters", [True, False])
def test_without_security_parameters(with_security_parameters):
    schema = {
        "openapi": "3.0.0",
        "info": {"title": "Blank API", "version": "1.0"},
        "servers": [{"url": "http://localhost/api"}],
        "paths": {"/test": {"get": {"responses": {"200": {"description": "OK"}}}}},
        "components": {
            "securitySchemes": {"basic_auth": {"type": "http", "scheme": "basic"}},
        },
        "security": [{"basic_auth": []}],
    }
    schema = schemathesis.openapi.from_dict(schema)
    schema.config.generation.update(with_security_parameters=with_security_parameters)

    @given(case=schema["/test"]["GET"].as_strategy())
    @settings(max_examples=20, suppress_health_check=list(HealthCheck))
    def test(case):
        if with_security_parameters:
            assert "Authorization" in case.headers
        else:
            assert case.headers == {}

    test()


@pytest.mark.parametrize("version", ["2.0", "3.0.2"])
def test_undefined_security_scheme_is_ignored(ctx, version):
    # When a security requirement references a scheme that is NOT defined in securitySchemes/securityDefinitions
    raw_schema = ctx.openapi.build_schema(
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
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/users"]["get"]
    # Then it should be ignored
    assert not list(operation.security.iter_parameters())


def test_invalid_security_requirement_types_are_ignored(ctx):
    # When security requirements contain non-dict elements
    raw_schema = ctx.openapi.build_schema(
        {
            "/test": {
                "get": {
                    "security": [
                        "invalid_string",
                        123,
                        ["invalid", "list"],
                        None,
                        # Valid: should be processed
                        {"ApiKeyAuth": []},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "securitySchemes": {
                "ApiKeyAuth": {"type": "apiKey", "name": "X-API-Key", "in": "header"},
            }
        },
    )

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/test"]["GET"]

    # Should not crash and should extract parameters from the valid requirement
    params = list(operation.security.iter_parameters())
    # Only the valid ApiKeyAuth requirement should be processed
    assert len(params) == 1
    assert params[0]["name"] == "X-API-Key"
    assert params[0]["in"] == "header"
