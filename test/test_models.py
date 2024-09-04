import json
import re
from unittest.mock import ANY

import pytest
import requests
from hypothesis import given, settings

import schemathesis
from schemathesis._compat import MultipleFailures
from schemathesis.constants import NOT_SET, SCHEMATHESIS_TEST_CASE_HEADER, USER_AGENT
from schemathesis.exceptions import CheckFailed, UsageError
from schemathesis.generation import DataGenerationMethod
from schemathesis.models import APIOperation, Case, CaseSource, Request, Response, TransitionId
from schemathesis.specs.openapi.checks import content_type_conformance, response_schema_conformance
from schemathesis.transports import WSGITransport, _merge_dict_to


@pytest.fixture
def schema_with_payload(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
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
    }
    empty_open_api_3_schema["components"] = {
        "requestBodies": {
            "Sample": {
                "required": True,
                "content": {"text/plain": {"schema": {"type": "object"}}},
            },
            "Ref": {"$ref": "#/components/requestBodies/Sample"},
        }
    }
    return schemathesis.from_dict(empty_open_api_3_schema, validate_schema=True)


def test_make_case_explicit_media_type(schema_with_payload):
    # When there is only one possible media type
    # And the `media_type` argument is passed to `make_case` explicitly
    for method in ("POST", "PUT", "PATCH"):
        case = schema_with_payload["/data"][method].make_case(body="<foo></foo>", media_type="text/xml")
        # Then this explicit media type should be in `case`
        assert case.media_type == "text/xml"


def test_make_case_automatic_media_type(schema_with_payload):
    # When there is only one possible media type
    # And the `media_type` argument is not passed to `make_case`
    for method in ("POST", "PUT", "PATCH"):
        case = schema_with_payload["/data"][method].make_case(body="foo")
        # Then it should be chosen automatically
        assert case.media_type == "text/plain"


def test_make_case_missing_media_type(empty_open_api_3_schema):
    # When there are multiple available media types
    empty_open_api_3_schema["paths"] = {
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
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    # And the `media_type` argument is not passed to `make_case`
    # Then there should be a usage error
    with pytest.raises(UsageError):
        schema["/data"]["POST"].make_case(body="foo")


def test_path(swagger_20):
    operation = APIOperation("/users/{name}", "GET", {}, swagger_20)
    case = operation.make_case(path_parameters={"name": "test"})
    assert case.formatted_path == "/users/test"


@pytest.mark.parametrize(
    "kwargs, expected",
    (
        ({"path_parameters": {"name": "test"}}, "Case(path_parameters={'name': 'test'})"),
        (
            {"path_parameters": {"name": "test"}, "query": {"q": 1}},
            "Case(path_parameters={'name': 'test'}, query={'q': 1})",
        ),
    ),
)
def test_case_repr(swagger_20, kwargs, expected):
    operation = APIOperation("/users/{name}", "GET", {}, swagger_20)
    case = operation.make_case(**kwargs)
    assert repr(case) == expected


@pytest.mark.parametrize("override", (False, True))
@pytest.mark.parametrize("converter", (lambda x: x, lambda x: x + "/"))
def test_as_transport_kwargs(override, server, base_url, swagger_20, converter):
    base_url = converter(base_url)
    operation = APIOperation("/success", "GET", {}, swagger_20)
    case = operation.make_case(cookies={"TOKEN": "secret"})
    if override:
        data = case.as_transport_kwargs(base_url)
    else:
        operation.base_url = base_url
        data = case.as_transport_kwargs()
    assert data == {
        "headers": {"User-Agent": USER_AGENT, SCHEMATHESIS_TEST_CASE_HEADER: ANY},
        "method": "GET",
        "params": None,
        "cookies": {"TOKEN": "secret"},
        "url": f"http://127.0.0.1:{server['port']}/api/success",
    }
    response = requests.request(**data)
    assert response.status_code == 200
    assert response.json() == {"success": True}


@pytest.mark.operations("create_user")
def test_mutate_body(openapi3_schema):
    operation = openapi3_schema["/users/"]["post"]
    case = operation.make_case()
    case.body = {"foo": "bar"}
    response = case.call()
    assert response.request.body == json.dumps(case.body).encode()
    openapi3_schema.transport = WSGITransport(42)
    assert case.as_transport_kwargs()["json"] == case.body


def test_reserved_characters_in_operation_name(swagger_20):
    # See GH-992
    # When an API operation name contains `:`
    operation = APIOperation("/foo:bar", "GET", {}, swagger_20)
    case = operation.make_case()
    # Then it should not be truncated during API call
    assert case.as_transport_kwargs("/")["url"] == "/foo:bar"


@pytest.mark.parametrize(
    "headers, expected",
    (
        (None, {"User-Agent": USER_AGENT, "X-Key": "foo"}),
        ({"User-Agent": "foo/1.0"}, {"User-Agent": "foo/1.0", "X-Key": "foo"}),
        ({"X-Value": "bar"}, {"X-Value": "bar", "User-Agent": USER_AGENT, "X-Key": "foo"}),
        ({"UsEr-agEnT": "foo/1.0"}, {"UsEr-agEnT": "foo/1.0", "X-Key": "foo"}),
    ),
)
def test_as_transport_kwargs_override_user_agent(server, openapi2_base_url, swagger_20, headers, expected):
    operation = APIOperation("/success", "GET", {}, swagger_20, base_url=openapi2_base_url)
    original_headers = headers.copy() if headers is not None else headers
    case = operation.make_case(headers=headers)
    data = case.as_transport_kwargs(headers={"X-Key": "foo"})
    expected[SCHEMATHESIS_TEST_CASE_HEADER] = ANY
    assert data == {
        "headers": expected,
        "method": "GET",
        "params": None,
        "cookies": None,
        "url": f"http://127.0.0.1:{server['port']}/api/success",
    }
    assert case.headers == original_headers
    response = requests.request(**data)
    assert response.status_code == 200
    assert response.json() == {"success": True}


@pytest.mark.parametrize("header", ("content-Type", "Content-Type"))
def test_as_transport_kwargs_override_content_type(empty_open_api_3_schema, header):
    empty_open_api_3_schema["paths"] = {
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
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    case = schema["/data"]["post"].make_case(body="<html></html>", media_type="text/plain")
    # When the `Content-Type` header is explicitly passed
    data = case.as_transport_kwargs(headers={header: "text/html"})
    # Then it should be used in network requests
    assert data == {
        "method": "POST",
        "data": b"<html></html>",
        "params": None,
        "cookies": None,
        "headers": {header: "text/html", "User-Agent": USER_AGENT, SCHEMATHESIS_TEST_CASE_HEADER: ANY},
        "url": "/data",
    }


@pytest.mark.parametrize("override", (False, True))
def test_call(override, base_url, swagger_20):
    operation = APIOperation("/success", "GET", {}, swagger_20)
    case = operation.make_case()
    if override:
        response = case.call(base_url)
    else:
        operation.base_url = base_url
        response = case.call()
    assert response.status_code == 200
    assert response.json() == {"success": True}


@pytest.mark.operations("success")
def test_call_and_validate(openapi3_schema_url):
    api_schema = schemathesis.from_uri(openapi3_schema_url)

    @given(case=api_schema["/success"]["GET"].as_strategy())
    @settings(max_examples=1, deadline=None)
    def test(case):
        case.call_and_validate()

    test()


@pytest.mark.operations("success")
def test_call_and_validate_for_asgi(fastapi_app):
    api_schema = schemathesis.from_dict(fastapi_app.openapi(), force_schema_version="30")

    @given(case=api_schema["/users"]["GET"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        with pytest.raises(RuntimeError, match="If you use the ASGI integration"):
            case.call_and_validate()

    test()


def test_case_partial_deepcopy(swagger_20):
    operation = APIOperation("/example/path", "GET", {}, swagger_20)
    media_type = "application/json"
    original_case = Case(
        operation=operation,
        generation_time=0.0,
        media_type=media_type,
        path_parameters={"test": "test"},
        headers={"Content-Type": "application/json"},
        cookies={"TOKEN": "secret"},
        query={"a": 1},
        body={"b": 1},
    )

    copied_case = original_case.partial_deepcopy()
    copied_case.operation.path = "/overwritten/path"
    copied_case.path_parameters["test"] = "overwritten"
    copied_case.headers["Content-Type"] = "overwritten"
    copied_case.cookies["TOKEN"] = "overwritten"
    copied_case.query["a"] = "overwritten"
    copied_case.body["b"] = "overwritten"
    assert copied_case.media_type == media_type

    assert original_case.operation.path == "/example/path"
    assert original_case.path_parameters["test"] == "test"
    assert original_case.headers["Content-Type"] == "application/json"
    assert original_case.cookies["TOKEN"] == "secret"
    assert original_case.query["a"] == 1
    assert original_case.body["b"] == 1


def test_case_partial_deepcopy_same_generated_code(swagger_20):
    operation = APIOperation("/example/path", "GET", {}, swagger_20)
    original_case = Case(
        operation=operation,
        generation_time=0.0,
        media_type="application/json",
        path_parameters={"test": "test"},
        headers={"Content-Type": "application/json"},
        cookies={"TOKEN": "secret"},
        query={"a": 1},
        body={"b": 1},
    )
    copied_case = original_case.partial_deepcopy()
    assert original_case.as_curl_command().replace(
        f" -H '{SCHEMATHESIS_TEST_CASE_HEADER}: {original_case.id}'", ""
    ) == copied_case.as_curl_command().replace(f" -H '{SCHEMATHESIS_TEST_CASE_HEADER}: {copied_case.id}'", "")


def test_case_partial_deepcopy_source(swagger_20):
    operation = APIOperation("/example/path", "GET", {}, swagger_20)
    original_case = Case(operation=operation, generation_time=0.0)
    response = requests.Response()
    response.status_code = 500
    original_case.source = CaseSource(
        case=Case(operation=operation, generation_time=0.0, query={"first": 1}),
        response=response,
        elapsed=1.0,
        overrides_all_parameters=True,
        transition_id=TransitionId(name="CustomLink", status_code="201"),
    )
    copied_case = original_case.partial_deepcopy()
    assert copied_case.source.case.query == original_case.source.case.query
    assert copied_case.source.response.status_code == original_case.source.response.status_code


def test_validate_response(testdir):
    testdir.make_test(
        r"""
from requests import Response, Request
from schemathesis.failures import UndefinedStatusCode

@schema.parametrize()
def test_(case):
    response = Response()
    response.headers["Content-Type"] = "application/json"
    response.status_code = 418
    request = Request(method="GET", url="http://localhost/v1/users")
    response.request = request.prepare()
    try:
        case.validate_response(response)
    except AssertionError as exc:
        assert len(exc.causes) == 1
        assert isinstance(exc.causes[0].context, UndefinedStatusCode)
        assert exc.args[0].split("\n") == [
          "",
          "",
          "1. Undocumented HTTP status code",
          "",
          "    Received: 418",
          "    Documented: 200",
          "",
          "[418] I'm a Teapot:",
          "",
          "    <EMPTY>",
          "",
          "Reproduce with: ",
          "",
          f"    curl -X GET http://localhost/v1/users",
          "",
    ]
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_validate_response_no_errors(testdir):
    testdir.make_test(
        r"""
from requests import Response

@schema.parametrize()
def test_(case):
    response = Response()
    response.headers["Content-Type"] = "application/json"
    response.status_code = 200
    assert case.validate_response(response) is None
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


@pytest.mark.parametrize("factory_type", ("httpx", "requests"))
@pytest.mark.parametrize(
    "response_schema, payload, schema_path, instance, instance_path",
    (
        ({"type": "object"}, [], ["type"], [], []),
        ({"$ref": "#/components/schemas/Foo"}, [], ["type"], [], []),
        (
            {"type": "object", "properties": {"foo": {"type": "object"}}},
            {"foo": 42},
            ["properties", "foo", "type"],
            42,
            ["foo"],
        ),
    ),
)
def test_validate_response_schema_path(
    response_factory,
    factory_type,
    empty_open_api_3_schema,
    response_schema,
    payload,
    schema_path,
    instance,
    instance_path,
):
    empty_open_api_3_schema["paths"] = {
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
    }
    empty_open_api_3_schema["components"] = {"schemas": {"Foo": {"type": "object"}}}
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    response = getattr(response_factory, factory_type)(content=json.dumps(payload).encode("utf-8"))
    with pytest.raises(CheckFailed) as exc:
        schema["/test"]["POST"].validate_response(response)
    assert exc.value.context.schema_path == schema_path
    assert exc.value.context.schema == {"type": "object"}
    assert exc.value.context.instance == instance
    assert exc.value.context.instance_path == instance_path


@pytest.mark.operations()
def test_response_from_requests(base_url):
    response = requests.get(f"{base_url}/cookies", timeout=1)
    serialized = Response.from_requests(response)
    assert serialized.deserialize_body() == b""
    assert serialized.status_code == 200
    assert serialized.http_version == "1.1"
    assert serialized.message == "OK"
    assert serialized.headers["Set-Cookie"] == ["foo=bar; Path=/", "baz=spam; Path=/"]


@pytest.mark.parametrize("body, expected", ((NOT_SET, None), (b"example", b"example")))
def test_from_case(swagger_20, body, expected):
    operation = APIOperation("/users/{name}", "GET", {}, swagger_20, base_url="http://127.0.0.1/api/v3")
    case = Case(
        operation,
        generation_time=0.0,
        path_parameters={"name": "test"},
        body=body,
        media_type="application/octet-stream",
    )
    session = requests.Session()
    request = Request.from_case(case, session)
    assert request.deserialize_body() == expected
    assert request.uri == "http://127.0.0.1/api/v3/users/test"


@pytest.mark.parametrize(
    "value, message",
    (
        ("/userz", "`/userz` not found. Did you mean `/users`?"),
        ("/what?", "`/what?` not found"),
    ),
)
def test_operation_path_suggestion(swagger_20, value, message):
    with pytest.raises(KeyError, match=re.escape(message)):
        swagger_20[value]["POST"]


def test_method_suggestion(swagger_20):
    with pytest.raises(KeyError, match="Method `PUT` not found. Available methods: GET"):
        swagger_20["/users"]["PUT"]


def test_deprecated_attribute(swagger_20):
    operation = APIOperation("/users/{name}", "GET", {}, swagger_20, base_url="http://127.0.0.1/api/v3")
    case = Case(operation, generation_time=0.0)
    with pytest.warns(Warning) as records:
        assert case.endpoint == case.operation == operation
    assert str(records[0].message) == (
        "Property `endpoint` is deprecated and will be removed in Schemathesis 4.0. Use `operation` instead."
    )


@pytest.mark.parametrize("method", DataGenerationMethod.all())
@pytest.mark.hypothesis_nested
def test_data_generation_method_is_available(method, empty_open_api_3_schema):
    # When a new case is generated
    empty_open_api_3_schema["paths"] = {
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

    api_schema = schemathesis.from_dict(empty_open_api_3_schema)

    @given(case=api_schema["/data"]["POST"].as_strategy(data_generation_method=method))
    @settings(max_examples=1)
    def test(case):
        # Then its data generation method should be available
        assert case.data_generation_method == method

    test()


@pytest.mark.hypothesis_nested
def test_case_insensitive_headers(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
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
    # When headers are generated
    schema = schemathesis.from_dict(empty_open_api_3_schema)

    @given(case=schema["/data"]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        assert "X-id" in case.headers
        # Then they are case-insensitive
        case.headers["x-ID"] = "foo"
        assert len(case.headers) == 1
        assert case.headers["X-id"] == "foo"

    test()


def test_iter_parameters(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
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
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    params = list(schema["/data"]["POST"].iter_parameters())
    assert len(params) == 2
    assert params[0].name == "X-id"
    assert params[1].name == "q"


def test_checks_errors_deduplication(empty_open_api_3_schema):
    # See GH-1394
    empty_open_api_3_schema["paths"] = {
        "/data": {
            "get": {
                "responses": {
                    "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "integer"}}}}
                },
            },
        },
    }
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    case = schema["/data"]["GET"].make_case()
    response = requests.Response()
    response.status_code = 200
    response.request = requests.PreparedRequest()
    response.request.prepare(method="GET", url="http://example.com")
    # When there are two checks that raise the same failure
    with pytest.raises(MultipleFailures, match="Missing Content-Type header") as exc:
        case.validate_response(response, checks=(content_type_conformance, response_schema_conformance))
    # Then the resulting output should be deduplicated
    assert "2. " not in str(exc.value)


def _assert_override(spy, arg, original, overridden):
    # Then it should override generated value
    # And keep other values of the same kind intact
    for key, value in {**original, **overridden}.items():
        kwargs = spy.call_args[1]
        assert kwargs[arg][key] == value
        assert all(key not in kwargs for key in overridden)


@pytest.mark.parametrize("arg", ("headers", "cookies", "params"))
def test_call_overrides(mocker, arg, openapi_30):
    spy = mocker.patch("requests.Session.request", side_effect=ValueError)
    original = {"A": "X", "B": "X"}
    case = Case(
        openapi_30["/users"]["GET"],
        generation_time=0.0,
        headers=original,
        cookies=original,
        query=original,
    )
    # When user passes header / cookie / query explicitly
    overridden = {"B": "Y"}
    try:
        case.call(**{arg: overridden}, base_url="http://127.0.0.1")
    except ValueError:
        pass
    _assert_override(spy, arg, original, overridden)


def test_merge_dict_to():
    data = {"params": {"A": 1}}
    _merge_dict_to(data, "params", {"B": 2})
    assert data == {"params": {"A": 1, "B": 2}}


@pytest.mark.parametrize("call_arg, client_arg", (("headers", "headers"), ("params", "query_string")))
def test_call_overrides_wsgi(mocker, call_arg, client_arg, openapi_30):
    spy = mocker.patch("werkzeug.Client.open", side_effect=ValueError)
    original = {"A": "X", "B": "X"}
    openapi_30.transport = WSGITransport(42)
    case = Case(
        openapi_30["/users"]["GET"],
        generation_time=0.0,
        headers=original,
        query=original,
    )
    # NOTE: Werkzeug does not accept cookies, so no override
    # When user passes header / query explicitly
    overridden = {"B": "Y"}
    try:
        case.call(**{call_arg: overridden}, base_url="http://127.0.0.1", app=42)
    except ValueError:
        pass
    _assert_override(spy, client_arg, original, overridden)


@pytest.mark.parametrize(
    "name, location, exists",
    (
        ("X-Key", "header", True),
        ("X-Key2", "header", False),
        ("X-Key", "cookie", False),
        ("X-Key", "query", False),
        ("key", "query", True),
        ("bla", "body", False),
        ("body", "body", True),
        ("unknown", "unknown", False),
    ),
)
def test_get_parameter(empty_open_api_3_schema, name, location, exists):
    empty_open_api_3_schema["paths"] = {
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
    }
    empty_open_api_3_schema["components"] = {
        "securitySchemes": {
            "ApiKeyAuth": {"type": "apiKey", "name": "X-Key", "in": "header"},
        }
    }
    empty_open_api_3_schema["security"] = [{"ApiKeyAuth": []}]
    schema = schemathesis.from_dict(empty_open_api_3_schema, validate_schema=True)

    parameter = schema["/data/"]["GET"].get_parameter(name, location)
    assert (parameter is not None) is exists
    if exists:
        assert parameter.name == name
        assert parameter.location == location
