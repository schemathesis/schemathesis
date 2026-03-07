import json

import httpx
import pytest
import requests
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request as WerkzeugRequest

import schemathesis
from schemathesis.core.failures import FailureGroup
from schemathesis.core.request import ParsedRequest
from schemathesis.openapi.checks import JsonSchemaError


def _werkzeug_request(path="/events", query_string="event_type=1", method="GET"):
    env = EnvironBuilder(method=method, path=path, query_string=query_string).get_environ()
    return WerkzeugRequest(env)


@pytest.mark.parametrize("request_obj", [
    requests.Request("GET", "http://localhost/events", params={"event_type": "1"}).prepare(),
    httpx.Request("GET", "http://localhost/events?event_type=1"),
    _werkzeug_request(),
])
def test_parsed_request_from_any(request_obj):
    parsed = ParsedRequest.from_any(request_obj)
    assert parsed.method == "GET"
    assert parsed.path == "/events"
    assert parsed.query == {"event_type": ["1"]}


@pytest.fixture
def simple_schema(ctx):
    raw = ctx.openapi.build_schema({
        "/events": {
            "get": {
                "parameters": [
                    {"in": "query", "name": "event_type", "schema": {"type": "integer"}}
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    })
    return schemathesis.openapi.from_dict(raw)


@pytest.fixture
def schema_with_path_param(ctx):
    raw = ctx.openapi.build_schema({
        "/users/{user_id}": {
            "get": {
                "parameters": [
                    {"in": "path", "name": "user_id", "required": True, "schema": {"type": "integer"}}
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    })
    return schemathesis.openapi.from_dict(raw)


@pytest.fixture
def schema_with_body(ctx):
    raw = ctx.openapi.build_schema({
        "/items": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "count": {"type": "integer"},
                                },
                                "required": ["name"],
                                "additionalProperties": False,
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}},
            }
        }
    })
    return schemathesis.openapi.from_dict(raw)


def test_validate_request_valid_query(simple_schema):
    r = requests.Request("GET", "http://localhost/events", params={"event_type": "1"}).prepare()
    simple_schema["/events"]["GET"].validate_request(r)  # must not raise


@pytest.mark.parametrize("params,expect_failure", [
    ({"event_type": "abc"}, True),
    ({"unknown_param": "x"}, True),
    ({"event_type": "1"}, False),
])
def test_validate_request_query_cases(simple_schema, params, expect_failure):
    r = requests.Request("GET", "http://localhost/events", params=params).prepare()
    if expect_failure:
        with pytest.raises((FailureGroup, JsonSchemaError)) as exc_info:
            simple_schema["/events"]["GET"].validate_request(r)
        exc = exc_info.value
        if isinstance(exc, FailureGroup):
            assert any(isinstance(f, JsonSchemaError) for f in exc.exceptions)
        else:
            assert isinstance(exc, JsonSchemaError)
    else:
        simple_schema["/events"]["GET"].validate_request(r)


@pytest.mark.parametrize("params,expected", [
    ({"event_type": "1"}, True),
    ({"unknown_param": "x"}, False),
])
def test_is_valid_request(simple_schema, params, expected):
    r = requests.Request("GET", "http://localhost/events", params=params).prepare()
    assert simple_schema["/events"]["GET"].is_valid_request(r) == expected


def test_validate_request_invalid_path_param(schema_with_path_param):
    r = requests.Request("GET", "http://localhost/users/abc").prepare()
    with pytest.raises((FailureGroup, JsonSchemaError)):
        schema_with_path_param["/users/{user_id}"]["GET"].validate_request(r)


def test_validate_request_valid_path_param(schema_with_path_param):
    r = requests.Request("GET", "http://localhost/users/42").prepare()
    schema_with_path_param["/users/{user_id}"]["GET"].validate_request(r)


@pytest.mark.parametrize("body,expect_failure", [
    ({"name": "thing", "count": 3}, False),
    ({"count": 3}, True),
    ({"name": "thing", "x": "y"}, True),
])
def test_validate_request_body(schema_with_body, body, expect_failure):
    encoded = json.dumps(body).encode()
    r = requests.Request(
        "POST", "http://localhost/items",
        data=encoded, headers={"Content-Type": "application/json"}
    ).prepare()
    if expect_failure:
        with pytest.raises((FailureGroup, JsonSchemaError)) as exc_info:
            schema_with_body["/items"]["POST"].validate_request(r)
        exc = exc_info.value
        if isinstance(exc, FailureGroup):
            assert any(isinstance(f, JsonSchemaError) for f in exc.exceptions)
        else:
            assert isinstance(exc, JsonSchemaError)
    else:
        schema_with_body["/items"]["POST"].validate_request(r)


def test_validate_request_empty_body_skipped(schema_with_body):
    r = requests.Request("POST", "http://localhost/items").prepare()
    schema_with_body["/items"]["POST"].validate_request(r)  # must not raise


@pytest.fixture
def schema_with_header(ctx):
    raw = ctx.openapi.build_schema({
        "/things": {
            "get": {
                "parameters": [
                    {"in": "header", "name": "X-Custom-Header", "required": True, "schema": {"type": "string"}}
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    })
    return schemathesis.openapi.from_dict(raw)


def test_validate_request_valid_header(schema_with_header):
    r = requests.Request("GET", "http://localhost/things", headers={"X-Custom-Header": "value"}).prepare()
    schema_with_header["/things"]["GET"].validate_request(r)  # must not raise


def test_validate_request_missing_required_header(schema_with_header):
    r = requests.Request("GET", "http://localhost/things").prepare()
    with pytest.raises((FailureGroup, JsonSchemaError)):
        schema_with_header["/things"]["GET"].validate_request(r)


@pytest.mark.parametrize("request_obj", [
    requests.Request("GET", "http://localhost/events", params={"unknown": "x"}).prepare(),
    httpx.Request("GET", "http://localhost/events?unknown=x"),
    _werkzeug_request(query_string="unknown=x"),
])
def test_validate_request_multi_transport_invalid(simple_schema, request_obj):
    with pytest.raises((FailureGroup, JsonSchemaError)):
        simple_schema["/events"]["GET"].validate_request(request_obj)


@pytest.mark.parametrize("request_obj", [
    requests.Request("GET", "http://localhost/events", params={"event_type": "1"}).prepare(),
    httpx.Request("GET", "http://localhost/events?event_type=1"),
    _werkzeug_request(query_string="event_type=1"),
])
def test_validate_request_multi_transport_valid(simple_schema, request_obj):
    simple_schema["/events"]["GET"].validate_request(request_obj)
