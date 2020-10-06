from test.utils import SIMPLE_PATH

import pytest
import requests
from hypothesis import given, settings

import schemathesis
from schemathesis.constants import USER_AGENT
from schemathesis.models import Case, Endpoint, Request, Response


def test_path(swagger_20):
    endpoint = Endpoint("/users/{name}", "GET", {}, swagger_20)
    case = Case(endpoint, path_parameters={"name": "test"})
    assert case.formatted_path == "/users/test"


@pytest.mark.parametrize("override", (False, True))
@pytest.mark.parametrize("converter", (lambda x: x, lambda x: x + "/"))
def test_as_requests_kwargs(override, server, base_url, swagger_20, converter):
    base_url = converter(base_url)
    endpoint = Endpoint("/success", "GET", {}, swagger_20)
    kwargs = {"endpoint": endpoint, "cookies": {"TOKEN": "secret"}}
    if override:
        case = Case(**kwargs)
        data = case.as_requests_kwargs(base_url)
    else:
        case = Case(**kwargs)
        endpoint.base_url = base_url
        data = case.as_requests_kwargs()
    assert data == {
        "headers": {"User-Agent": USER_AGENT},
        "json": None,
        "method": "GET",
        "params": None,
        "cookies": {"TOKEN": "secret"},
        "url": f"http://127.0.0.1:{server['port']}/api/success",
    }
    response = requests.request(**data)
    assert response.status_code == 200
    assert response.json() == {"success": True}


@pytest.mark.parametrize(
    "headers, expected",
    (
        (None, {"User-Agent": USER_AGENT, "X-Key": "foo"}),
        ({"User-Agent": "foo/1.0"}, {"User-Agent": "foo/1.0", "X-Key": "foo"}),
        ({"X-Value": "bar"}, {"X-Value": "bar", "User-Agent": USER_AGENT, "X-Key": "foo"}),
        ({"UsEr-agEnT": "foo/1.0"}, {"UsEr-agEnT": "foo/1.0", "X-Key": "foo"}),
    ),
)
def test_as_requests_kwargs_override_user_agent(server, openapi2_base_url, swagger_20, headers, expected):
    endpoint = Endpoint("/success", "GET", {}, swagger_20, base_url=openapi2_base_url)
    original_headers = headers.copy() if headers is not None else headers
    kwargs = {"endpoint": endpoint, "headers": headers}
    case = Case(**kwargs)
    data = case.as_requests_kwargs(headers={"X-Key": "foo"})
    assert data == {
        "headers": expected,
        "json": None,
        "method": "GET",
        "params": None,
        "cookies": None,
        "url": f"http://127.0.0.1:{server['port']}/api/success",
    }
    assert case.headers == original_headers
    response = requests.request(**data)
    assert response.status_code == 200
    assert response.json() == {"success": True}


@pytest.mark.parametrize("override", (False, True))
@pytest.mark.filterwarnings("always")
def test_call(override, base_url, swagger_20):
    endpoint = Endpoint("/success", "GET", {}, swagger_20)
    kwargs = {"endpoint": endpoint}
    if override:
        case = Case(**kwargs)
        response = case.call(base_url)
    else:
        case = Case(**kwargs)
        endpoint.base_url = base_url
        response = case.call()
    assert response.status_code == 200
    assert response.json() == {"success": True}
    with pytest.warns(None) as records:
        del response
    assert not records


@pytest.mark.endpoints("success")
def test_call_and_validate(openapi3_schema_url):
    api_schema = schemathesis.from_uri(openapi3_schema_url)

    @given(case=api_schema["/success"]["GET"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        case.call_and_validate()

    test()


def test_case_partial_deepcopy(swagger_20):
    endpoint = Endpoint("/example/path", "GET", {}, swagger_20)
    original_case = Case(
        endpoint=endpoint,
        path_parameters={"test": "test"},
        headers={"Content-Type": "application/json"},
        cookies={"TOKEN": "secret"},
        query={"a": 1},
        body={"b": 1},
        form_data={"first": "John", "last": "Doe"},
    )

    copied_case = original_case.partial_deepcopy()
    copied_case.endpoint.path = "/overwritten/path"
    copied_case.path_parameters["test"] = "overwritten"
    copied_case.headers["Content-Type"] = "overwritten"
    copied_case.cookies["TOKEN"] = "overwritten"
    copied_case.query["a"] = "overwritten"
    copied_case.body["b"] = "overwritten"
    copied_case.form_data["first"] = "overwritten"

    assert original_case.endpoint.path == "/example/path"
    assert original_case.path_parameters["test"] == "test"
    assert original_case.headers["Content-Type"] == "application/json"
    assert original_case.cookies["TOKEN"] == "secret"
    assert original_case.query["a"] == 1
    assert original_case.body["b"] == 1
    assert original_case.form_data["first"] == "John"


schema = schemathesis.from_path(SIMPLE_PATH)
ENDPOINT = Endpoint("/api/success", "GET", {}, base_url="http://example.com", schema=schema)


@pytest.mark.parametrize(
    "case, expected",
    (
        # Body can be of any primitive type supported by Open API
        (
            Case(ENDPOINT, body={"test": 1}),
            f"requests.get('http://example.com/api/success', "
            f"headers={{'User-Agent': '{USER_AGENT}'}}, json={{'test': 1}})",
        ),
        (
            Case(ENDPOINT, body=["foo"]),
            f"requests.get('http://example.com/api/success', headers={{'User-Agent': '{USER_AGENT}'}}, json=['foo'])",
        ),
        (
            Case(ENDPOINT, body="foo"),
            f"requests.get('http://example.com/api/success', headers={{'User-Agent': '{USER_AGENT}'}}, json='foo')",
        ),
        (
            Case(ENDPOINT, body=1),
            f"requests.get('http://example.com/api/success', headers={{'User-Agent': '{USER_AGENT}'}}, json=1)",
        ),
        (
            Case(ENDPOINT, body=1.1),
            f"requests.get('http://example.com/api/success', headers={{'User-Agent': '{USER_AGENT}'}}, json=1.1)",
        ),
        (
            Case(ENDPOINT, body=True),
            f"requests.get('http://example.com/api/success', headers={{'User-Agent': '{USER_AGENT}'}}, json=True)",
        ),
        (Case(ENDPOINT), f"requests.get('http://example.com/api/success', headers={{'User-Agent': '{USER_AGENT}'}})"),
        (
            Case(ENDPOINT, query={"a": 1}),
            f"requests.get('http://example.com/api/success', "
            f"headers={{'User-Agent': '{USER_AGENT}'}}, params={{'a': 1}})",
        ),
    ),
)
def test_get_code_to_reproduce(case, expected):
    assert case.get_code_to_reproduce() == expected


def test_validate_response(testdir):
    testdir.make_test(
        fr"""
from requests import Response

@schema.parametrize()
def test_(case):
    response = Response()
    response.headers["Content-Type"] = "application/json"
    response.status_code = 418
    try:
        case.validate_response(response)
    except AssertionError as exc:
        assert exc.args[0].split("\n") == [
          '',
          '',
          '1. Received a response with a status code, which is not defined in the schema: 418',
          '',
          'Declared status codes: 200',
          '',
          '----------',
          '',
          'Run this Python code to reproduce this response: ',
          '',
          "    requests.get('http://localhost/v1/users', headers={{'User-Agent': '{USER_AGENT}'}})"
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


@pytest.mark.endpoints()
def test_response_from_requests(base_url):
    response = requests.get(f"{base_url}/cookies")
    serialized = Response.from_requests(response)
    assert serialized.status_code == 200
    assert serialized.http_version == "1.1"
    assert serialized.message == "OK"
    assert serialized.headers["Set-Cookie"] == ["foo=bar; Path=/", "baz=spam; Path=/"]


@pytest.mark.parametrize(
    "base_url, expected",
    (
        (None, "http://127.0.0.1/api/v3/users/test"),
        ("http://127.0.0.1/api/v3", "http://127.0.0.1/api/v3/users/test"),
    ),
)
def test_from_case(swagger_20, base_url, expected):
    endpoint = Endpoint("/users/{name}", "GET", {}, swagger_20, base_url="http://127.0.0.1/api/v3")
    case = Case(endpoint, path_parameters={"name": "test"})
    session = requests.Session()
    request = Request.from_case(case, session)
    assert request.uri == "http://127.0.0.1/api/v3/users/test"
