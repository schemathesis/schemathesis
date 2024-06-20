"""OpenAPI specific loader behavior."""

import json
from pathlib import Path

import pytest
from flask import Flask, Response

import schemathesis
from schemathesis.exceptions import SchemaError
from schemathesis.extra._flask import run_server as run_flask_server
from schemathesis.specs.openapi import loaders
from schemathesis.specs.openapi.loaders import NON_STRING_OBJECT_KEY_MESSAGE, SCHEMA_LOADING_ERROR, SCHEMA_SYNTAX_ERROR
from schemathesis.specs.openapi.schemas import OpenApi30, SwaggerV20


def test_openapi_asgi_loader(fastapi_app, run_test):
    # When an ASGI app is loaded via `from_asgi`
    schema = loaders.from_asgi("/openapi.json", fastapi_app, force_schema_version="30")
    strategy = schema["/users"]["GET"].as_strategy()
    # Then it should successfully make calls
    run_test(strategy)


def test_openapi_wsgi_loader(flask_app, run_test):
    # When a WSGI app is loaded via `from_wsgi`
    schema = loaders.from_wsgi("/schema.yaml", flask_app)
    strategy = schema["/success"]["GET"].as_strategy()
    # Then it should successfully make calls
    run_test(strategy)


@pytest.mark.parametrize(
    "version, schema, expected",
    (
        ("20", {"swagger": "3.0.0"}, SwaggerV20),
        ("30", {"openapi": "2.0"}, OpenApi30),
        ("30", {"openapi": "3.1.0"}, OpenApi30),
    ),
)
def test_force_open_api_version(version, schema, expected):
    loaded = loaders.from_dict(schema, force_schema_version=version, validate_schema=False)
    assert isinstance(loaded, expected)


@pytest.mark.parametrize(
    "version, expected",
    (
        ("3.1.0", "The provided schema uses Open API 3.1.0, which is currently not fully supported."),
        ("3.2.0", "The provided schema uses Open API 3.2.0, which is currently not supported."),
    ),
)
def test_unsupported_openapi_version(version, expected):
    with pytest.raises(SchemaError, match=expected):
        loaders.from_dict({"openapi": version}, validate_schema=False)


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
    with pytest.raises(
        SchemaError, match="Unable to determine the Open API version as it's not specified in the document."
    ):
        # Then it raises an error
        loaders.from_dict({})


@pytest.mark.parametrize(
    "content_type, expected",
    (
        (True, SCHEMA_LOADING_ERROR),
        (None, SCHEMA_LOADING_ERROR),
        ("application/json", SCHEMA_SYNTAX_ERROR),
        ("application/x-yaml", SCHEMA_SYNTAX_ERROR),
    ),
)
def test_invalid_content_type(httpserver, content_type, expected: str):
    # When the user tries to load an HTML as a schema
    content = """
<html>
<style>
  html {
    margin: 0;
    background: #fafafa;
  }
</style>
<html>
    """
    response = Response(response=content)
    if content_type is None:
        del response.headers["Content-Type"]
    elif content_type is not True:
        response.headers["Content-Type"] = content_type
    path = "/openapi/"
    handler = httpserver.expect_request(path)
    handler.respond_with_response(response)
    schema_url = httpserver.url_for(path)
    # And loading cause an error
    # Then it should be suggested to the user that they should provide JSON or YAML
    with pytest.raises(SchemaError, match=expected):
        schemathesis.from_uri(schema_url)


@pytest.mark.parametrize(
    "value, expected",
    (
        ("file.json", True),
        ("file.txt", False),
    ),
)
@pytest.mark.parametrize("type_", (Path, str))
def test_is_json_path(type_, value, expected):
    assert loaders._is_json_path(type_(value)) == expected


def test_numeric_status_codes(empty_open_api_3_schema):
    # When the API schema contains a numeric status code, which is not allowed by the spec
    empty_open_api_3_schema["paths"] = {
        "/foo": {
            "parameters": [],
            "get": {
                "responses": {200: {"description": "OK"}},
            },
            "post": {
                "responses": {201: {"description": "OK"}},
            },
        },
    }
    # And schema validation is enabled
    # Then Schemathesis reports an error about numeric status codes
    with pytest.raises(SchemaError, match="Numeric HTTP status codes detected in your YAML schema") as exc:
        schemathesis.from_dict(empty_open_api_3_schema, validate_schema=True)
    # And shows all locations of these keys
    assert " - 200 at schema['paths']['/foo']['get']['responses']" in exc.value.message
    assert " - 201 at schema['paths']['/foo']['post']['responses']" in exc.value.message


def test_non_string_keys(empty_open_api_3_schema):
    # If API schema contains a non-string key
    empty_open_api_3_schema[True] = 42
    # Then it should be reported with a proper message
    with pytest.raises(SchemaError, match=NON_STRING_OBJECT_KEY_MESSAGE):
        schemathesis.from_dict(empty_open_api_3_schema, validate_schema=True)


JSON_ERROR = ["Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"]
YAML_ERROR = [
    "unacceptable character #x0080: control characters are not allowed",
    '  in "<unicode string>", position 2',
]


@pytest.mark.parametrize(
    "schema_url, content_type, payload, expected",
    (
        ("openapi.json", "application/json", b"{1", JSON_ERROR),
        ("openapi.yaml", "text/yaml", b'{"\x80": 1}', YAML_ERROR),
    ),
)
def test_parsing_errors_uri(schema_url, content_type, payload, expected):
    app = Flask("test_app")

    @app.route(f"/{schema_url}")
    def schema():
        return Response(payload, content_type=content_type)

    port = run_flask_server(app)

    with pytest.raises(SchemaError) as exc:
        schemathesis.from_uri(f"http://127.0.0.1:{port}/{schema_url}")
    assert exc.value.extras == expected


@pytest.mark.parametrize(
    "schema_path, content_type, payload, expected",
    (
        ("openapi.json", "application/json", "{1", JSON_ERROR),
        ("openapi.yaml", "text/yaml", '{"\x80": 1}', YAML_ERROR),
    ),
)
def test_parsing_errors_path(testdir, schema_path, content_type, payload, expected):
    name, ext = schema_path.split(".")
    schema_file = testdir.makefile(f".{ext}", **{name: payload})

    with pytest.raises(SchemaError) as exc:
        schemathesis.from_path(str(schema_file))

    assert exc.value.extras == expected
