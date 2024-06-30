import pytest
from hypothesis import HealthCheck, given, settings

import schemathesis
from schemathesis.generation import GenerationConfig
from schemathesis.specs.openapi.references import InliningResolver
from schemathesis.specs.openapi.security import OpenAPISecurityProcessor


def test_ref_resolving():
    http_schema = {"basic_auth": {"type": "http", "scheme": "basic"}}
    schema = {
        "openapi": "3.0.0",
        "info": {"title": "Blank API", "version": "1.0"},
        "servers": [{"url": "http://localhost/api"}],
        "paths": {"foo": {"get": {"responses": {"200": {"description": "OK"}}}}},
        "components": {"securitySchemes": {"$ref": "#/components/HTTPSchema"}, "HTTPSchema": http_schema},
    }
    resolver = InliningResolver("", schema)
    assert OpenAPISecurityProcessor().get_security_definitions(schema, resolver) == http_schema


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
    resolver = InliningResolver("", schema)
    assert OpenAPISecurityProcessor().get_security_definitions(schema, resolver) == {"basic_auth": http_schema}


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
    "kwargs, expected",
    (
        ({}, "EXPECTED"),
        ({"headers": {"TestApiKey": "EXPLICIT"}}, "EXPLICIT"),
    ),
)
def test_name_clash(kwargs, expected):
    # Operation definition should take precedence over security schemes
    # Explicit headers should take precedence over everything else
    schema = schemathesis.from_dict(SCHEMA_WITH_PARAMETER_AND_SECURITY_SCHEME, validate_schema=True)

    @given(case=schema["/test"]["GET"].as_strategy(**kwargs))
    def test(case):
        assert case.headers[PARAMETER_NAME] == expected

    test()


@pytest.mark.parametrize("with_security_parameters", (True, False))
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
    schema = schemathesis.from_dict(
        schema,
        validate_schema=True,
        generation_config=GenerationConfig(with_security_parameters=with_security_parameters),
    )

    @given(case=schema["/test"]["GET"].as_strategy())
    @settings(max_examples=20, suppress_health_check=list(HealthCheck))
    def test(case):
        if with_security_parameters:
            assert "Authorization" in case.headers
        else:
            assert case.headers is None

    test()
