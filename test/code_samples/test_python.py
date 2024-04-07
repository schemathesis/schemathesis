import json

import hypothesis
import pytest
import requests
from hypothesis import HealthCheck, given, settings

from schemathesis.code_samples import _escape_single_quotes
from schemathesis.models import Case
from schemathesis.runner import from_schema


@pytest.fixture
def openapi_case(request, swagger_20):
    kwargs = getattr(request, "param", {})
    # Tests that use this fixture may provide payloads, but it doesn't matter what HTTP method it used in this context
    operation = swagger_20["/users"]["get"]
    operation.base_url = "http://127.0.0.1:1"
    return operation.make_case(media_type="application/json", **kwargs)


def get_full_code(url_params="", data=None):
    url = f"http://127.0.0.1:1/users{url_params}"
    if data:
        data = json.dumps(data)
        data = f", data=b'{data}', headers={{'Content-Type': 'application/json'}}"
    return f"requests.get('{url}'{data})"


@pytest.mark.parametrize(
    "openapi_case, url_params, data_repr",
    (
        # Body can be of any primitive type supported by Open API
        ({"body": {"test": 1}}, "", {"test": 1}),
        ({"body": ["foo"]}, "", ["foo"]),
        ({"body": "foo"}, "", "foo"),
        ({"body": 1}, "", 1),
        ({"body": 1.1}, "", 1.1),
        ({"body": True}, "", True),
        ({}, "", ""),
        ({"query": {"a": 1}}, "?a=1", ""),
    ),
    indirect=["openapi_case"],
)
def test_open_api_code_sample(openapi_case, url_params, data_repr):
    # Custom request parts should be correctly displayed
    code = openapi_case.get_code_to_reproduce()
    assert code == get_full_code(url_params, data_repr), code
    # And the generated code should be valid Python
    with pytest.raises(requests.exceptions.ConnectionError):
        eval(code)


@pytest.mark.parametrize("verify", (True, False))
def test_code_sample_from_request(openapi_case, verify):
    url = "http://example.com/api/success"
    request = requests.Request(method="GET", url=url).prepare()
    # By default, Schemathesis uses User-agent header, but it is possible to remove it (e.g. via hooks in CLI)
    # `Case.get_code_to_reproduce` should be able to generate a code sample for any `requests.Request`
    code_to_reproduce = openapi_case.get_code_to_reproduce(request=request, verify=verify)
    if not verify:
        assert code_to_reproduce == f"requests.get('{url}', verify=False)"
    else:
        assert code_to_reproduce == f"requests.get('{url}')"


@pytest.mark.hypothesis_nested
def test_get_code_sample_code_validity(mocker, loose_schema):
    # See GH-1030
    # When the input schema is too loose
    original = Case.as_transport_kwargs

    def as_transport_kwargs(*args, **kwargs):
        kwargs = original(*args, **kwargs)
        # Add timeout in order to ensure fast execution on Windows
        kwargs["timeout"] = 0.001
        return kwargs

    mocker.patch.object(Case, "as_transport_kwargs", as_transport_kwargs)

    @given(case=loose_schema["/test/{key}"]["POST"].as_strategy())
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much], deadline=None)
    def test(case):
        code = case.get_code_to_reproduce()
        # Then generated code should always be syntactically valid
        with pytest.raises((requests.exceptions.ConnectionError, requests.Timeout)):
            eval(code)

    test()


@pytest.mark.parametrize(
    "value, expected",
    (
        ("http://example.com", "http://example.com"),
        ("http://example.com'", "http://example.com\\'"),
        ("http://example.com\\'", "http://example.com\\'"),
        ("http://example.com\\\\'", "http://example.com\\\\\\'"),
    ),
)
def test_escape_single_quotes(value, expected):
    escaped = _escape_single_quotes(value)
    assert escaped == expected
    eval(f"'{escaped}'")


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_graphql_code_sample(graphql_url, graphql_schema, graphql_strategy):
    case = graphql_strategy.example()
    kwargs = case.as_transport_kwargs()
    request = requests.Request(**kwargs).prepare()
    assert (
        case.get_code_to_reproduce()
        == f"requests.post('{graphql_url}', data={repr(request.body)}, headers={{'Content-Type': 'application/json'}})"
    )


@pytest.mark.operations("failure")
def test_cli_output(cli, base_url, schema_url, snapshot_cli):
    assert cli.run(schema_url, "--code-sample-style=python") == snapshot_cli


@pytest.mark.operations("failure")
def test_reproduce_code_with_overridden_headers(any_app_schema, base_url):
    headers = {"X-Token": "test"}
    *_, after, finished = from_schema(
        any_app_schema,
        headers=headers.copy(),
        hypothesis_settings=hypothesis.settings(max_examples=1),
    ).execute()
    assert finished.has_failures
    for key, value in headers.items():
        assert after.result.checks[1].example.headers[key] == value
