"""OpenAPI specific loader behavior."""
import json

import pytest

from schemathesis.specs.openapi import loaders
from schemathesis.specs.openapi.schemas import OpenApi30, SwaggerV20


def test_openapi_asgi_loader(fastapi_app, run_asgi_test):
    # When an ASGI app is loaded via `from_asgi`
    schema = loaders.from_asgi("/openapi.json", fastapi_app)
    strategy = schema["/users"]["GET"].as_strategy()
    # Then it should successfully make calls via `call_asgi`
    run_asgi_test(strategy)


def test_openapi_wsgi_loader(flask_app, run_wsgi_test):
    # When a WSGI app is loaded via `from_wsgi`
    schema = loaders.from_wsgi("/schema.yaml", flask_app)
    strategy = schema["/success"]["GET"].as_strategy()
    # Then it should successfully make calls via `call_wsgi`
    run_wsgi_test(strategy)


@pytest.mark.parametrize(
    "version, expected",
    (
        ("20", SwaggerV20),
        ("30", OpenApi30),
    ),
)
def test_force_open_api_version(version, expected):
    schema = {
        # Invalid schema, but it happens in real applications
        "swagger": "2.0",
        "openapi": "3.0.0",
    }
    loaded = loaders.from_dict(schema, force_schema_version=version, validate_schema=False)
    assert isinstance(loaded, expected)


def test_number_deserializing(testdir):
    # When numbers in a schema are written in scientific notation but without a dot
    # (achieved by dumping the schema with json.dumps)
    schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/teapot": {
                "get": {
                    "summary": "Test",
                    "parameters": [
                        {
                            "name": "key",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "number", "multipleOf": 0.00001},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }

    schema_path = testdir.makefile(".yaml", schema=json.dumps(schema))
    # Then yaml loader should parse them without schema validation errors
    parsed = loaders.from_path(str(schema_path))
    # and the value should be a number
    value = parsed.raw_schema["paths"]["/teapot"]["get"]["parameters"][0]["schema"]["multipleOf"]
    assert isinstance(value, float)


def test_unsupported_type():
    # When Schemathesis can't detect the Open API spec version
    with pytest.raises(ValueError, match="^Unsupported schema type$"):
        # Then it raises an error
        loaders.from_dict({})
