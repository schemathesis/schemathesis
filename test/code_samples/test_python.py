import hypothesis
import pytest
import requests
from flask import Flask
from hypothesis import HealthCheck, given, settings

import schemathesis
from schemathesis.constants import USER_AGENT
from schemathesis.runner import from_schema


@pytest.fixture
def openapi_case(request, swagger_20):
    kwargs = getattr(request, "param", {})
    # Tests that use this fixture may provide payloads, but it doesn't matter what HTTP method it used in this context
    operation = swagger_20["/users"]["get"]
    operation.base_url = "http://127.0.0.1:1"
    return operation.make_case(media_type="application/json", **kwargs)


def get_full_code(kwargs_repr=""):
    # Simple way to detect json for these tests
    if kwargs_repr.startswith("json"):
        headers = ", 'Content-Type': 'application/json'"
    else:
        headers = ""
    if kwargs_repr:
        kwargs_repr = f", {kwargs_repr}"
    return (
        f"requests.get('http://127.0.0.1:1/users', " f"headers={{'User-Agent': '{USER_AGENT}'{headers}}}{kwargs_repr})"
    )


@pytest.mark.parametrize(
    "openapi_case, kwargs_repr",
    (
        # Body can be of any primitive type supported by Open API
        ({"body": {"test": 1}}, "json={'test': 1}"),
        ({"body": ["foo"]}, "json=['foo']"),
        ({"body": "foo"}, "json='foo'"),
        ({"body": 1}, "json=1"),
        ({"body": 1.1}, "json=1.1"),
        ({"body": True}, "json=True"),
        ({}, ""),
        ({"query": {"a": 1}}, "params={'a': 1}"),
    ),
    indirect=["openapi_case"],
)
def test_open_api_code_sample(openapi_case, kwargs_repr):
    # Custom request parts should be correctly displayed
    code = openapi_case.get_code_to_reproduce()
    assert code == get_full_code(kwargs_repr), code
    # And the generated code should be valid Python
    with pytest.raises(requests.exceptions.ConnectionError):
        eval(code)


def test_code_sample_from_request(openapi_case):
    url = "http://example.com/api/success"
    request = requests.Request(method="GET", url=url).prepare()
    # By default Schemathesis uses User-agent header, but it is possible to remove it (e.g. via hooks in CLI)
    # `Case.get_code_to_reproduce` should be able to generate a code sample for any `requests.Request`
    assert openapi_case.get_code_to_reproduce(request=request) == f"requests.get('{url}')"


@pytest.mark.hypothesis_nested
def test_get_code_sample_code_validity(empty_open_api_2_schema):
    # See GH-1030
    # When the input schema is too loose
    empty_open_api_2_schema["paths"] = {
        "/test/{key}": {
            "post": {
                "parameters": [{"name": "key", "in": "path"}],
                "responses": {"default": {"description": "OK"}},
            }
        }
    }
    schema = schemathesis.from_dict(empty_open_api_2_schema, base_url="http://127.0.0.1:1", validate_schema=False)
    strategy = schema["/test/{key}"]["POST"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much], deadline=None)
    def test(case):
        code = case.get_code_to_reproduce()
        # Then generated code should always be syntactically valid
        with pytest.raises(requests.exceptions.ConnectionError):
            eval(code)

    test()


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_graphql_code_sample(graphql_url, graphql_schema, graphql_strategy):
    case = graphql_strategy.example()
    assert (
        case.get_code_to_reproduce() == f"requests.post('{graphql_url}', "
        f"headers={{'User-Agent': '{USER_AGENT}'}}, "
        f"json={{'query': {repr(case.body)}}})"
    )


@pytest.mark.operations("failure")
def test_cli_output(cli, base_url, schema_url):
    result = cli.run(schema_url, "--code-sample-style=python")
    lines = result.stdout.splitlines()
    assert "Run this Python code to reproduce this failure: " in lines
    headers = (
        f"{{'User-Agent': '{USER_AGENT}', 'Accept-Encoding': 'gzip, deflate', "
        f"'Accept': '*/*', 'Connection': 'keep-alive'}}"
    )
    assert f"    requests.get('{base_url}/failure', headers={headers})" in lines


@pytest.mark.operations("failure")
def test_reproduce_code_with_overridden_headers(any_app_schema, base_url):
    # Note, headers are essentially the same, but keys are ordered differently due to implementation details of
    # real vs wsgi apps. It is the simplest solution, but not the most flexible one, though.
    if isinstance(any_app_schema.app, Flask):
        headers = {
            "User-Agent": USER_AGENT,
            "X-Token": "test",
            "Accept-Encoding": "gzip, deflate",
            "Accept": "*/*",
            "Connection": "keep-alive",
        }
        expected = f"requests.get('http://localhost/api/failure', headers={headers})"
    else:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "*/*",
            "Connection": "keep-alive",
            "X-Token": "test",
        }
        expected = f"requests.get('{base_url}/failure', headers={headers})"

    *_, after, finished = from_schema(
        any_app_schema, headers=headers, hypothesis_settings=hypothesis.settings(max_examples=1)
    ).execute()
    assert finished.has_failures
    assert after.result.checks[1].example.requests_code == expected
