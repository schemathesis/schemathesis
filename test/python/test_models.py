import json
import re
from unittest.mock import ANY

import pytest
import requests
from hypothesis import HealthCheck, given, settings

import schemathesis
from schemathesis.checks import not_a_server_error
from schemathesis.core import SCHEMATHESIS_TEST_CASE_HEADER
from schemathesis.core.errors import IncorrectUsage
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import merge_at
from schemathesis.core.transport import USER_AGENT, Response
from schemathesis.generation import GenerationMode
from schemathesis.schemas import APIOperation
from schemathesis.specs.openapi.checks import content_type_conformance, response_schema_conformance
from schemathesis.transport.prepare import get_default_headers


@pytest.fixture
def schema_with_payload(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"text/plain": {"schema": {"type": "string"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                },
                "put": {
                    "requestBody": {"$ref": "#/components/requestBodies/Sample"},
                    "responses": {"200": {"description": "OK"}},
                },
                "patch": {
                    "requestBody": {"$ref": "#/components/requestBodies/Ref"},
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
        components={
            "requestBodies": {
                "Sample": {
                    "required": True,
                    "content": {"text/plain": {"schema": {"type": "object"}}},
                },
                "Ref": {"$ref": "#/components/requestBodies/Sample"},
            }
        },
    )
    return schemathesis.openapi.from_dict(schema)


def test_make_case_explicit_media_type(schema_with_payload):
    # When there is only one possible media type
    # And the `media_type` argument is passed to `make_case` explicitly
    for method in ("POST", "PUT", "PATCH"):
        case = schema_with_payload["/data"][method].Case(body="<foo></foo>", media_type="text/xml")
        # Then this explicit media type should be in `case`
        assert case.media_type == "text/xml"


def test_make_case_automatic_media_type(schema_with_payload):
    # When there is only one possible media type
    # And the `media_type` argument is not passed to `make_case`
    for method in ("POST", "PUT", "PATCH"):
        case = schema_with_payload["/data"][method].Case(body="foo")
        # Then it should be chosen automatically
        assert case.media_type == "text/plain"


def test_make_case_missing_media_type(ctx):
    # When there are multiple available media types
    schema = ctx.openapi.build_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "text/plain": {"schema": {"type": "string"}},
                            "application/json": {"schema": {"type": "array"}},
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )
    schema = schemathesis.openapi.from_dict(schema)
    # And the `media_type` argument is not passed to `make_case`
    # Then there should be a usage error
    with pytest.raises(IncorrectUsage):
        schema["/data"]["POST"].Case(body="foo")


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"path_parameters": {"name": "test"}}, "Case(path_parameters={'name': 'test'})"),
        (
            {"path_parameters": {"name": "test"}, "query": {"q": 1}},
            "Case(path_parameters={'name': 'test'}, query={'q': 1})",
        ),
    ],
)
def test_case_repr(swagger_20, kwargs, expected):
    operation = APIOperation(
        "/users/{name}",
        "GET",
        {},
        swagger_20,
        responses=swagger_20._parse_responses({}, ""),
        security=swagger_20._parse_security({}),
    )
    case = operation.Case(**kwargs)
    assert repr(case) == expected


@pytest.mark.parametrize("override", [False, True])
@pytest.mark.parametrize("converter", [lambda x: x, lambda x: x + "/"])
def test_as_transport_kwargs(override, server, base_url, swagger_20, converter):
    base_url = converter(base_url)
    operation = APIOperation(
        "/success",
        "GET",
        {},
        swagger_20,
        responses=swagger_20._parse_responses({}, ""),
        security=swagger_20._parse_security({}),
    )
    case = operation.Case(cookies={"TOKEN": "secret"})
    if override:
        data = case.as_transport_kwargs(base_url)
    else:
        operation.base_url = base_url
        data = case.as_transport_kwargs()
    assert data == {
        "headers": {**get_default_headers(), "User-Agent": USER_AGENT, SCHEMATHESIS_TEST_CASE_HEADER: ANY},
        "method": "GET",
        "params": {},
        "cookies": {"TOKEN": "secret"},
        "url": f"http://127.0.0.1:{server['port']}/api/success",
    }
    response = requests.request(**data)
    assert response.status_code == 200
    assert response.json() == {"success": True}


@pytest.mark.operations("create_user")
def test_mutate_body(openapi3_schema):
    operation = openapi3_schema["/users/"]["post"]
    case = operation.Case(body={"foo": "bar"})
    response = case.call()
    assert response.request.body == json.dumps(case.body).encode()
    openapi3_schema.app = 42
    assert case.as_transport_kwargs()["json"] == case.body


def test_reserved_characters_in_operation_name(swagger_20):
    # See GH-992
    # When an API operation name contains `:`
    operation = APIOperation(
        "/foo:bar",
        "GET",
        {},
        swagger_20,
        responses=swagger_20._parse_responses({}, ""),
        security=swagger_20._parse_security({}),
    )
    case = operation.Case()
    # Then it should not be truncated during API call
    assert case.as_transport_kwargs("/")["url"] == "/foo:bar"


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, {"User-Agent": USER_AGENT, "X-Key": "foo"}),
        ({"User-Agent": "foo/1.0"}, {"User-Agent": "foo/1.0", "X-Key": "foo"}),
        ({"X-Value": "bar"}, {"X-Value": "bar", "User-Agent": USER_AGENT, "X-Key": "foo"}),
        ({"UsEr-agEnT": "foo/1.0"}, {"UsEr-agEnT": "foo/1.0", "X-Key": "foo"}),
    ],
)
def test_as_transport_kwargs_override_user_agent(server, openapi2_base_url, swagger_20, headers, expected):
    operation = APIOperation(
        "/success",
        "GET",
        {},
        swagger_20,
        base_url=openapi2_base_url,
        responses=swagger_20._parse_responses({}, ""),
        security=swagger_20._parse_security({}),
    )
    original_headers = headers.copy()
    case = operation.Case(headers=headers)
    data = case.as_transport_kwargs(headers={"X-Key": "foo"})
    expected[SCHEMATHESIS_TEST_CASE_HEADER] = ANY
    assert data == {
        "headers": {**get_default_headers(), **expected},
        "method": "GET",
        "params": {},
        "cookies": {},
        "url": f"http://127.0.0.1:{server['port']}/api/success",
    }
    assert case.headers == original_headers
    response = requests.request(**data)
    assert response.status_code == 200
    assert response.json() == {"success": True}


@pytest.mark.parametrize("header", ["content-Type", "Content-Type"])
def test_as_transport_kwargs_override_content_type(ctx, header):
    schema = ctx.openapi.build_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"text/plain": {"schema": {"type": "string"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )
    schema = schemathesis.openapi.from_dict(schema)
    case = schema["/data"]["post"].Case(body="<html></html>", media_type="text/plain")
    # When the `Content-Type` header is explicitly passed
    data = case.as_transport_kwargs(headers={header: "text/html"})
    # Then it should be used in network requests
    assert data == {
        "method": "POST",
        "data": b"<html></html>",
        "params": {},
        "cookies": {},
        "headers": {
            **get_default_headers(),
            header: "text/html",
            "User-Agent": USER_AGENT,
            SCHEMATHESIS_TEST_CASE_HEADER: ANY,
        },
        "url": "/data",
    }


@pytest.mark.parametrize("override", [False, True])
def test_call(override, base_url, swagger_20):
    operation = APIOperation(
        "/success",
        "GET",
        {},
        swagger_20,
        responses=swagger_20._parse_responses({}, ""),
        security=swagger_20._parse_security({}),
    )
    case = operation.Case()
    if override:
        response = case.call(base_url)
    else:
        operation.base_url = base_url
        response = case.call()
    assert response.status_code == 200
    assert response.json() == {"success": True}


def custom_check(ctx, response, case):
    pass


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"additional_checks": (custom_check,)},
        {"excluded_checks": (not_a_server_error,)},
    ],
)
@pytest.mark.operations("success")
def test_call_and_validate(openapi3_schema_url, kwargs):
    api_schema = schemathesis.openapi.from_url(openapi3_schema_url)

    @given(case=api_schema["/success"]["GET"].as_strategy())
    @settings(max_examples=1, deadline=None)
    def test(case):
        case.call_and_validate(**kwargs)

    test()


@pytest.mark.operations("custom_format")
def test_metadata_has_only_relevant_components(openapi3_schema_url):
    api_schema = schemathesis.openapi.from_url(openapi3_schema_url)

    operation = api_schema["/custom_format"]["GET"]

    @given(
        case=operation.as_strategy(generation_mode=GenerationMode.POSITIVE)
        | operation.as_strategy(generation_mode=GenerationMode.NEGATIVE)
    )
    @settings(max_examples=10, deadline=None)
    def test(case):
        # Metadata should only contain components relevant to the API operation
        assert len(case.meta.components) == 1
        assert ParameterLocation.QUERY in case.meta.components

    test()


@pytest.mark.operations("success")
def test_call_and_validate_for_asgi(fastapi_app):
    api_schema = schemathesis.openapi.from_dict(fastapi_app.openapi())

    @given(case=api_schema["/users"]["GET"].as_strategy())
    @settings(max_examples=1, deadline=None, suppress_health_check=list(HealthCheck))
    def test(case):
        with pytest.raises(IncorrectUsage, match="If you use the ASGI integration"):
            case.call_and_validate()

    test()


def test_validate_response(testdir):
    testdir.make_test(
        r"""
import pytest
from requests import Response, Request
from schemathesis.openapi.checks import UndefinedStatusCode
from schemathesis.core.failures import FailureGroup

@schema.parametrize()
def test_(case):
    response = Response()
    response.headers["Content-Type"] = "application/json"
    response.status_code = 418
    request = Request(method="GET", url="http://localhost/v1/users")
    response.request = request.prepare()
    with pytest.raises(FailureGroup):
        case.validate_response(response)
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_validate_response_no_errors(testdir):
    testdir.make_test(
        r"""
import requests
from schemathesis.core.transport import Response
from unittest.mock import Mock

class Headers(dict):

    def getlist(self, key):
        v = self.get(key)
        if v is not None:
            return [v]

@schema.parametrize()
def test_(case):
    response = requests.Response()
    response._content = b"{}"
    response.headers["Content-Type"] = "application/json"

    response.raw = Mock(headers=Headers({"Content-Type": "application/json"}))
    response.status_code = 200
    request = requests.PreparedRequest()
    request.prepare("GET", "http://127.0.0.1")
    response.request = request
    assert case.validate_response(response) is None
""",
        generation_modes=[GenerationMode.POSITIVE],
    )
    result = testdir.runpytest("--tb=long", "-sv")
    result.assert_outcomes(passed=1)


@pytest.mark.parametrize("factory_type", ["httpx", "requests", "wsgi"])
@pytest.mark.parametrize(
    ("response_schema", "payload", "schema_path", "instance", "instance_path"),
    [
        ({"type": "object"}, [], ["type"], [], []),
        ({"$ref": "#/components/schemas/Foo"}, [], ["type"], [], []),
        (
            {"type": "object", "properties": {"foo": {"type": "object"}}},
            {"foo": 42},
            ["properties", "foo", "type"],
            42,
            ["foo"],
        ),
    ],
)
def test_validate_response_schema_path(
    ctx,
    response_factory,
    factory_type,
    response_schema,
    payload,
    schema_path,
    instance,
    instance_path,
):
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": response_schema}},
                        },
                    },
                },
            }
        },
        components={"schemas": {"Foo": {"type": "object"}}},
    )
    schema = schemathesis.openapi.from_dict(schema)
    response = getattr(response_factory, factory_type)(content=json.dumps(payload).encode("utf-8"))
    with pytest.raises(Failure) as exc:
        schema["/test"]["POST"].validate_response(response)
    assert not schema["/test"]["POST"].is_valid_response(response)
    failure = exc.value
    assert failure.schema_path == schema_path
    assert failure.schema == {"type": "object"}
    assert failure.instance == instance
    assert failure.instance_path == instance_path


@pytest.mark.operations
def test_response_from_requests(base_url):
    response = requests.get(f"{base_url}/cookies", timeout=1)
    serialized = Response.from_requests(response, True)
    assert serialized.content == b""
    assert serialized.status_code == 200
    assert serialized.http_version == "1.1"
    assert serialized.message == "OK"
    assert serialized.headers["set-cookie"] == ["foo=bar; Path=/", "baz=spam; Path=/"]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("/userz", "`/userz` not found. Did you mean `/users`?"),
        ("/what?", "`/what?` not found"),
    ],
)
def test_operation_path_suggestion(swagger_20, value, message):
    with pytest.raises(LookupError, match=re.escape(message)):
        swagger_20[value]["POST"]


def test_method_suggestion(swagger_20):
    with pytest.raises(LookupError, match="Method `PUT` not found. Available methods: GET"):
        swagger_20["/users"]["PUT"]


def test_method_suggestion_without_parameters(swagger_20):
    swagger_20.raw_schema["paths"]["/users"]["parameters"] = []
    swagger_20.raw_schema["paths"]["/users"]["x-ext"] = []
    with pytest.raises(LookupError, match="Method `PUT` not found. Available methods: GET$"):
        swagger_20["/users"]["PUT"]


@pytest.mark.parametrize("mode", list(GenerationMode))
@pytest.mark.hypothesis_nested
def test_generation_mode_is_available(ctx, mode):
    # When a new case is generated
    schema = ctx.openapi.build_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"text/plain": {"schema": {"type": "string"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )

    api_schema = schemathesis.openapi.from_dict(schema)

    @given(case=api_schema["/data"]["POST"].as_strategy(generation_mode=mode))
    @settings(max_examples=1)
    def test(case):
        # Then its generator mode should be available
        assert case.meta.generation.mode == mode

    test()


@pytest.mark.hypothesis_nested
def test_case_insensitive_headers(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/data": {
                "post": {
                    "parameters": [
                        {
                            "name": "X-id",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )
    # When headers are generated
    schema = schemathesis.openapi.from_dict(schema)

    @given(case=schema["/data"]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        assert "X-id" in case.headers
        # Then they are case-insensitive
        case.headers["x-ID"] = "foo"
        assert len(case.headers) == 1
        assert case.headers["X-id"] == "foo"

    test()


def test_iter_parameters(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/data": {
                "post": {
                    "parameters": [
                        {
                            "name": "X-id",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "q",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )
    schema = schemathesis.openapi.from_dict(schema)
    params = list(schema["/data"]["POST"].iter_parameters())
    assert len(params) == 2
    assert params[0].name == "X-id"
    assert params[1].name == "q"


@pytest.mark.parametrize("factory_type", ["httpx", "requests", "wsgi"])
def test_checks_errors_deduplication(ctx, response_factory, factory_type):
    # See GH-1394
    schema = ctx.openapi.build_schema(
        {
            "/data": {
                "get": {
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "integer"}}}}
                    },
                },
            },
        }
    )
    schema = schemathesis.openapi.from_dict(schema)
    case = schema["/data"]["GET"].Case()
    response = getattr(response_factory, factory_type)(content=b"42", content_type=None)
    # When there are two checks that raise the same failure
    with pytest.raises(FailureGroup) as exc:
        case.validate_response(response, checks=(content_type_conformance, response_schema_conformance))
    assert len(exc.value.exceptions) == 1


def test_operation_hash(openapi_30):
    # API Operations should be hashable
    _ = {i.ok() for i in openapi_30.get_all_operations()}


def _assert_override(spy, arg, original, overridden):
    # Then it should override generated value
    # And keep other values of the same kind intact
    for key, value in {**original, **overridden}.items():
        kwargs = spy.call_args[1]
        assert kwargs[arg][key] == value
        assert all(key not in kwargs for key in overridden)


@pytest.mark.parametrize("arg", ["headers", "cookies"])
def test_call_overrides(mocker, arg, openapi_30):
    spy = mocker.patch("requests.Session.request", side_effect=ValueError)
    original = {"A": "X", "B": "X"}
    case = openapi_30["/users"]["GET"].Case(headers=original, cookies=original, query=original)
    # When user passes header / cookie / query explicitly
    overridden = {"B": "Y"}
    try:
        case.call(**{arg: overridden}, base_url="http://127.0.0.1")
    except ValueError:
        pass
    _assert_override(spy, arg, original, overridden)


@pytest.mark.parametrize("with_config", [True, False])
@pytest.mark.parametrize(
    "kwargs",
    [
        {"verify": False},
        {"cert": "abc"},
        {"timeout": 42},
    ],
)
def test_call_transport_overrides(mocker, with_config, kwargs, openapi_30):
    spy = mocker.patch("requests.Session.request", side_effect=ValueError)
    if with_config:
        # Config should be overridden anyway
        openapi_30.config.tls_verify = "/tmp"
        openapi_30.config.request_cert = "/tmp"
        openapi_30.config.request_timeout = 0.5
    case = openapi_30["/users"]["GET"].Case()
    try:
        case.call(**kwargs, base_url="http://127.0.0.1")
    except ValueError:
        pass
    for key, value in kwargs.items():
        assert spy.call_args[1][key] == value


def test_merge_at():
    data = {"params": {"A": 1}}
    merge_at(data, "params", {"B": 2})
    assert data == {"params": {"A": 1, "B": 2}}


@pytest.mark.parametrize(("call_arg", "client_arg"), [("headers", "headers"), ("params", "query_string")])
def test_call_overrides_wsgi(mocker, call_arg, client_arg, openapi_30):
    spy = mocker.patch("werkzeug.Client.open", side_effect=ValueError)
    original = {"A": "X", "B": "X"}
    openapi_30.app = 42
    case = openapi_30["/users"]["GET"].Case(headers=original, query=original)
    # NOTE: Werkzeug does not accept cookies, so no override
    # When user passes header / query explicitly
    overridden = {"B": "Y"}
    try:
        case.call(**{call_arg: overridden}, base_url="http://127.0.0.1", app=42)
    except ValueError:
        pass
    _assert_override(spy, client_arg, original, overridden)


@pytest.mark.parametrize(
    ("name", "location", "exists"),
    [
        ("X-Key", "header", True),
        ("X-Key2", "header", False),
        ("X-Key", "cookie", False),
        ("X-Key", "query", False),
        ("key", "query", True),
        ("bla", "body", False),
        ("body", "body", True),
        ("unknown", "unknown", False),
    ],
)
def test_get_parameter(ctx, name, location, exists):
    schema = ctx.openapi.build_schema(
        {
            "/data/": {
                "get": {
                    "parameters": [
                        {
                            "name": name,
                            "in": location,
                            "required": True,
                            "schema": {"type": "string"},
                        }
                        for name, location in (
                            ("X-Key", "header"),
                            ("key", "query"),
                        )
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "text/plain": {"schema": {"type": "string"}},
                            "application/json": {"schema": {"type": "array"}},
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "securitySchemes": {
                "ApiKeyAuth": {"type": "apiKey", "name": "X-Key", "in": "header"},
            }
        },
        security=[{"ApiKeyAuth": []}],
    )
    schema = schemathesis.openapi.from_dict(schema)

    parameter = schema["/data/"]["GET"].get_parameter(name, location)
    assert (parameter is not None) is exists
    if exists:
        assert parameter.name == name
        assert parameter.location == location
