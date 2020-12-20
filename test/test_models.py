from test.utils import SIMPLE_PATH

import pytest
import requests
from hypothesis import given, settings

import schemathesis
from schemathesis.constants import USER_AGENT
from schemathesis.models import Case, Endpoint, Request, Response


def test_path(swagger_20):
    endpoint = Endpoint("/users/{name}", "GET", {}, swagger_20)
    case = endpoint.make_case(path_parameters={"name": "test"})
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
    endpoint = Endpoint("/users/{name}", "GET", {}, swagger_20)
    case = endpoint.make_case(**kwargs)
    assert repr(case) == expected


@pytest.mark.parametrize("override", (False, True))
@pytest.mark.parametrize("converter", (lambda x: x, lambda x: x + "/"))
def test_as_requests_kwargs(override, server, base_url, swagger_20, converter):
    base_url = converter(base_url)
    endpoint = Endpoint("/success", "GET", {}, swagger_20)
    case = endpoint.make_case(cookies={"TOKEN": "secret"})
    if override:
        data = case.as_requests_kwargs(base_url)
    else:
        endpoint.base_url = base_url
        data = case.as_requests_kwargs()
    assert data == {
        "headers": {"User-Agent": USER_AGENT},
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
    case = endpoint.make_case(headers=headers)
    data = case.as_requests_kwargs(headers={"X-Key": "foo"})
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


@pytest.mark.parametrize("override", (False, True))
@pytest.mark.filterwarnings("always")
def test_call(override, base_url, swagger_20):
    endpoint = Endpoint("/success", "GET", {}, swagger_20)
    case = endpoint.make_case()
    if override:
        response = case.call(base_url)
    else:
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
    )

    copied_case = original_case.partial_deepcopy()
    copied_case.endpoint.path = "/overwritten/path"
    copied_case.path_parameters["test"] = "overwritten"
    copied_case.headers["Content-Type"] = "overwritten"
    copied_case.cookies["TOKEN"] = "overwritten"
    copied_case.query["a"] = "overwritten"
    copied_case.body["b"] = "overwritten"

    assert original_case.endpoint.path == "/example/path"
    assert original_case.path_parameters["test"] == "test"
    assert original_case.headers["Content-Type"] == "application/json"
    assert original_case.cookies["TOKEN"] == "secret"
    assert original_case.query["a"] == 1
    assert original_case.body["b"] == 1


schema = schemathesis.from_path(SIMPLE_PATH)
ENDPOINT = Endpoint("/api/success", "POST", {}, base_url="http://example.com", schema=schema)


def make_case(**kwargs):
    return Case(ENDPOINT, media_type="application/json", **kwargs)


def expected(payload=""):
    # Simple way to detect json for these tests
    if payload.startswith("json"):
        headers = ", 'Content-Type': 'application/json'"
    else:
        headers = ""
    if payload:
        payload = f", {payload}"
    return (
        f"requests.post('http://example.com/api/success', "
        f"headers={{'User-Agent': '{USER_AGENT}'{headers}}}{payload})"
    )


@pytest.mark.parametrize(
    "case, expected",
    (
        # Body can be of any primitive type supported by Open API
        (make_case(body={"test": 1}), expected("json={'test': 1}")),
        (make_case(body=["foo"]), expected("json=['foo']")),
        (make_case(body="foo"), expected("json='foo'")),
        (make_case(body=1), expected("json=1")),
        (make_case(body=1.1), expected("json=1.1")),
        (make_case(body=True), expected("json=True")),
        (make_case(), expected()),
        (make_case(query={"a": 1}), expected("params={'a': 1}")),
    ),
)
def test_get_code_to_reproduce(case, expected):
    assert case.get_code_to_reproduce() == expected, case.get_code_to_reproduce()


def test_code_to_reproduce():
    case = Case(Endpoint("/api/success", "GET", {}, base_url="http://127.0.0.1:1", schema=schema), body={"foo": 42})
    request = requests.Request(**case.as_requests_kwargs()).prepare()
    code = case.get_code_to_reproduce(request=request)
    with pytest.raises(requests.exceptions.ConnectionError):
        eval(code)


def test_code_to_reproduce_without_extra_args():
    case = Case(Endpoint("/api/success", "GET", {}, base_url="http://0.0.0.0", schema=schema))
    request = requests.Request(method="GET", url="http://0.0.0.0/api/success").prepare()
    code = case.get_code_to_reproduce(request=request)
    assert code == "requests.get('http://0.0.0.0/api/success')"


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
          "",
          "",
          "1. Received a response with a status code, which is not defined in the schema: 418",
          "",
          "Declared status codes: 200",
          "",
          "----------",
          "",
          "Response payload: ``",
          "",
          "Run this Python code to reproduce this response: ",
          "",
          "    requests.get('http://localhost/v1/users', headers={{'User-Agent': '{USER_AGENT}'}})",
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
