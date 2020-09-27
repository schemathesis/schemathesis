import json

import pytest
from requests.models import Response
from yarl import URL

import schemathesis
from schemathesis.constants import USER_AGENT

from .utils import SIMPLE_PATH, make_schema


def test_path_loader(simple_schema):
    # Each loader method should read the specified schema correctly
    assert schemathesis.from_path(SIMPLE_PATH).raw_schema == simple_schema


def test_uri_loader(app_schema, app, schema_url):
    # Each loader method should read the specified schema correctly
    assert schemathesis.from_uri(schema_url).raw_schema == app_schema


def test_uri_loader_custom_kwargs(app, schema_url):
    # All custom kwargs are passed to `requests.get`
    schemathesis.from_uri(schema_url, verify=False, headers={"X-Test": "foo"})
    request = app["schema_requests"][0]
    assert request.headers["X-Test"] == "foo"
    assert request.headers["User-Agent"] == USER_AGENT


def test_base_url(openapi3_schema_url):
    schema = schemathesis.from_uri(openapi3_schema_url)
    assert schema.base_url is None


@pytest.mark.parametrize("url", ("http://example.com/", "http://example.com"))
def test_base_url_override(openapi3_schema_url, url):
    schema = schemathesis.from_uri(openapi3_schema_url, base_url=url)
    endpoint = next(schema.get_all_endpoints())
    assert endpoint.base_url == "http://example.com"


def test_port_override(openapi3_schema_url):
    schema = schemathesis.from_uri(openapi3_schema_url, port=8081)
    endpoint = next(schema.get_all_endpoints())
    assert endpoint.base_url == "http://127.0.0.1:8081/schema.yaml"


def test_port_override_with_ipv6(openapi3_schema_url, simple_openapi, mocker):
    resp = Response()
    resp.status_code = 200
    resp._content = json.dumps(simple_openapi).encode("utf-8")
    mocker.patch("requests.get", return_value=resp)

    url = URL(openapi3_schema_url)
    parts = list(map(int, url.host.split(".")))
    ipv6_host = "2002:{:02x}{:02x}:{:02x}{:02x}::".format(*parts)
    ipv6 = url.with_host("[%s]" % ipv6_host)
    schema = schemathesis.from_uri(str(ipv6), validate_schema=False, port=8081)
    endpoint = next(schema.get_all_endpoints())
    assert endpoint.base_url == "http://[2002:7f00:1::]:8081/schema.yaml"


def test_unsupported_type():
    with pytest.raises(ValueError, match="^Unsupported schema type$"):
        schemathesis.from_dict({})


@pytest.mark.parametrize("operation_id", ("bar_get", "bar_post"))
def test_operation_id(operation_id):
    parameters = {"responses": {"200": {"description": "OK"}}}
    raw = make_schema(
        "simple_openapi.yaml",
        paths={
            "/foo": {"get": {**parameters, "operationId": "foo_get"}},
            "/bar": {
                "get": {**parameters, "operationId": "bar_get"},
                "post": {**parameters, "operationId": "bar_post"},
                "put": parameters,
            },
        },
    )
    schema = schemathesis.from_dict(raw, operation_id=operation_id)

    assert schema.operation_id == operation_id

    assert len(list(schema.get_all_endpoints())) == 1
    for endpoint in schema.get_all_endpoints():
        assert endpoint.definition.raw["operationId"] == operation_id


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
    parsed = schemathesis.from_path(str(schema_path))
    # and the value should be a number
    value = parsed.raw_schema["paths"]["/teapot"]["get"]["parameters"][0]["schema"]["multipleOf"]
    assert isinstance(value, float)
