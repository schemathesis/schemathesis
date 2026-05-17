"""OpenAPI specific loader behavior."""

import io
import json
import platform

import pytest
import yaml
from fastapi import FastAPI
from flask import Flask, Response

import schemathesis
from schemathesis.core.errors import LoaderError
from test.utils import make_schema


def test_openapi_asgi_loader(run_test):
    # When an ASGI app is loaded via `from_asgi`
    app = FastAPI()

    @app.get("/users")
    async def users():
        return {"success": True}

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)
    strategy = schema["/users"]["GET"].as_strategy()
    # Then it should successfully make calls
    run_test(strategy)


def test_openapi_wsgi_loader(ctx, run_test):
    # When a WSGI app is loaded via `from_wsgi`
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_wsgi("/openapi.json", api.wsgi_app)
    strategy = schema["/api/success"]["GET"].as_strategy()
    # Then it should successfully make calls
    run_test(strategy)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("4.0.0", "The provided schema uses Open API 4.0.0, which is currently not supported."),
    ],
)
def test_unsupported_openapi_version(version, expected):
    with pytest.raises(LoaderError, match=expected):
        schemathesis.openapi.from_dict({"openapi": version})


def test_number_deserializing(ctx, testdir):
    # Scientific-notation numbers without a dot (via json.dumps) must round-trip through the YAML loader.
    schema = ctx.openapi.build_schema(
        {
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
    )

    schema_path = testdir.makefile(".yaml", schema=json.dumps(schema))
    # Then yaml loader should parse them without schema validation errors
    parsed = schemathesis.openapi.from_path(str(schema_path))
    # and the value should be a number
    value = parsed.raw_schema["paths"]["/teapot"]["get"]["parameters"][0]["schema"]["multipleOf"]
    assert isinstance(value, float)


def test_unsupported_type():
    # When Schemathesis can't detect the Open API spec version
    with pytest.raises(
        LoaderError, match="Unable to determine the Open API version as it's not specified in the document."
    ):
        # Then it raises an error
        schemathesis.openapi.from_dict({})


if platform.python_implementation() == "PyPy":
    JSON_ERROR = ["Key name must be string at char: line 1 column 2 (char 1)"]
else:
    JSON_ERROR = ["Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"]
YAML_ERROR = [
    "unacceptable character #x0080: control characters are not allowed",
    '  in "<unicode string>", position 2',
]


@pytest.mark.parametrize(
    ("schema_url", "content_type", "payload", "expected"),
    [
        ("openapi.json", "application/json", b"{1", JSON_ERROR),
        ("openapi.yaml", "text/yaml", b'{"\x80": 1}', YAML_ERROR),
    ],
)
def test_parsing_errors_uri(schema_url, content_type, payload, expected, app_runner):
    app = Flask("test_app")

    @app.route(f"/{schema_url}")
    def schema():
        return Response(payload, content_type=content_type)

    port = app_runner.run_flask_app(app)

    with pytest.raises(LoaderError) as exc:
        schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/{schema_url}")
    assert exc.value.extras == expected


def test_unknown_content_type_retry_yaml(app_runner):
    payload = make_schema("simple_openapi.yaml")
    payload = yaml.safe_dump(payload)

    app = Flask("test_app")

    @app.route("/schema")
    def schema():
        return Response(payload, content_type="application/vnd.oai.openapi; charset=utf-8")

    port = app_runner.run_flask_app(app)

    schemathesis.openapi.from_url(f"http://127.0.0.1:{port}/schema")


@pytest.mark.parametrize(
    ("schema_path", "payload", "expected"),
    [
        ("openapi.json", "{1", JSON_ERROR),
        ("openapi.yaml", '{"\x80": 1}', YAML_ERROR),
    ],
)
def test_parsing_errors_path(testdir, schema_path, payload, expected):
    name, ext = schema_path.split(".")
    schema_file = testdir.makefile(f".{ext}", **{name: payload})

    with pytest.raises(LoaderError) as exc:
        schemathesis.openapi.from_path(str(schema_file))

    assert exc.value.extras == expected


@pytest.mark.parametrize(
    "data",
    ['{"openapi": "3.0.0"}', "openapi: 3.0.0"],
)
def test_from_file(data) -> None:
    for input_data in (data, io.StringIO(data)):
        assert schemathesis.openapi.from_file(input_data).raw_schema == {"openapi": "3.0.0"}


@pytest.mark.parametrize(
    "data",
    [
        "{invalid json",
        "invalid: yaml:\nindentation",
        "",
        "   \n  \t  ",
    ],
)
def test_from_file_invalid_input(data: str) -> None:
    for input_data in (data, io.StringIO(data)):
        with pytest.raises(LoaderError):
            schemathesis.openapi.from_file(input_data)
