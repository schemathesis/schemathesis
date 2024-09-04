from urllib.parse import urlencode

import pytest
import requests
from requests import Request, Response

from schemathesis.constants import NOT_SET
from schemathesis.generation import DataGenerationMethod
from schemathesis.models import CaseSource, Check, Status, TestPhase, TransitionId
from schemathesis.models import Request as SerializedRequest
from schemathesis.models import Response as SerializedResponse
from schemathesis.runner.serialization import SerializedCheck, SerializedInteraction
from schemathesis.sanitization import (
    DEFAULT_KEYS_TO_SANITIZE,
    DEFAULT_REPLACEMENT,
    DEFAULT_SENSITIVE_MARKERS,
    Config,
    configure,
    sanitize_case,
    sanitize_history,
    sanitize_output,
    sanitize_request,
    sanitize_serialized_check,
    sanitize_serialized_interaction,
    sanitize_url,
)


@pytest.fixture
def request_factory():
    def factory(url="http://127.0.0.1", headers=None):
        request = Request(url=url, headers=headers or {})
        return request.prepare()

    return factory


@pytest.fixture
def sanitized_case_factory_factory(case_factory):
    def factory(keys_to_sanitize=DEFAULT_KEYS_TO_SANITIZE, default_replacement=DEFAULT_REPLACEMENT, **kwargs):
        case = case_factory(**kwargs)
        config = Config(keys_to_sanitize=keys_to_sanitize, replacement=default_replacement)
        sanitize_case(case, config=config)
        return case

    return factory


@pytest.mark.parametrize(
    "attr, initial, expected",
    [
        ("path_parameters", {"password": "1234"}, {"password": "[Filtered]"}),
        ("headers", {"Authorization": "Bearer token"}, {"Authorization": "[Filtered]"}),
        ("headers", {"Authorization": ["Bearer token"]}, {"Authorization": ["[Filtered]"]}),
        ("headers", {"X-Foo-Authorization": "Bearer token"}, {"X-Foo-Authorization": "[Filtered]"}),
        ("cookies", {"session": "xyz"}, {"session": "[Filtered]"}),
        ("query", {"api_key": "5678"}, {"api_key": "[Filtered]"}),
        ("body", {"nested": {"password": "password"}}, {"nested": {"password": "[Filtered]"}}),
    ],
)
def test_sanitize_case(sanitized_case_factory_factory, attr, initial, expected):
    case = sanitized_case_factory_factory(**{attr: initial})
    assert getattr(case, attr) == expected


def test_sanitize_case_body_not_dict_or_not_set(sanitized_case_factory_factory):
    assert (
        sanitized_case_factory_factory(body="Some string body").body == "Some string body"
    )  # Body should remain unchanged


def test_sanitize_case_body_is_not_set(sanitized_case_factory_factory):
    assert sanitized_case_factory_factory(body=NOT_SET).body is NOT_SET  # Body should remain unchanged


def test_sanitize_case_custom_keys_to_sanitize(sanitized_case_factory_factory):
    case = sanitized_case_factory_factory(query={"custom_key": "sensitive"}, keys_to_sanitize=("custom_key",))
    assert case.query["custom_key"] == "[Filtered]"


def test_sanitize_case_custom_replacement(sanitized_case_factory_factory):
    custom_replacement = "[Redacted]"
    case = sanitized_case_factory_factory(path_parameters={"password": "1234"}, default_replacement=custom_replacement)
    assert case.path_parameters["password"] == custom_replacement


@pytest.mark.parametrize(
    "body, expected",
    [
        ({"nested": {"secret": "reveal"}, "foo": 123}, {"nested": {"secret": "[Filtered]"}, "foo": 123}),
        ([{"secret": "reveal"}, 1], [{"secret": "[Filtered]"}, 1]),
        ("string body", "string body"),
        (123, 123),
        (NOT_SET, NOT_SET),
    ],
)
def test_sanitize_case_body_variants(sanitized_case_factory_factory, body, expected):
    assert sanitized_case_factory_factory(body=body).body == expected


def test_sanitize_history(case_factory):
    case3 = case_factory(headers={"Authorization": "Bearer token"})
    source3 = CaseSource(
        case=case3,
        response=requests.Response(),
        elapsed=0.3,
        overrides_all_parameters=True,
        transition_id=TransitionId(name="CustomLink", status_code="201"),
    )

    case2 = case_factory(headers={"X-API-Key": "12345"}, source=source3)
    source2 = CaseSource(
        case=case2,
        response=requests.Response(),
        elapsed=0.2,
        overrides_all_parameters=True,
        transition_id=TransitionId(name="CustomLink", status_code="201"),
    )

    case1 = case_factory(headers={"Password": "password"}, source=source2)
    source1 = CaseSource(
        case=case1,
        response=requests.Response(),
        elapsed=0.1,
        overrides_all_parameters=True,
        transition_id=TransitionId(name="CustomLink", status_code="201"),
    )

    sanitize_history(source1)

    assert case1.headers == {"Password": "[Filtered]"}
    assert case2.headers == {"X-API-Key": "[Filtered]"}
    assert case3.headers == {"Authorization": "[Filtered]"}


def test_sanitize_history_empty(case_factory):
    case = case_factory(headers={"Password": "password"})
    source = CaseSource(
        case=case,
        response=requests.Response(),
        elapsed=0.1,
        overrides_all_parameters=True,
        transition_id=TransitionId(name="CustomLink", status_code="201"),
    )

    sanitize_history(source)

    assert case.headers == {"Password": "[Filtered]"}


@pytest.mark.parametrize(
    "headers, expected",
    (
        ({"Authorization": "Bearer token"}, {"Authorization": "[Filtered]"}),
        ({"Custom-Token": "custom_token_value"}, {"Custom-Token": "[Filtered]"}),
        ({"Content-Type": "application/json"}, {"Content-Type": "application/json"}),
    ),
)
def test_sanitize_request(request_factory, headers, expected):
    request = request_factory(headers=headers)
    sanitize_request(request)
    assert request.headers == {**expected, "Content-Length": "0"}


def test_sanitize_request_url(request_factory):
    request = request_factory(url="http://user:pass@127.0.0.1/path")
    sanitize_request(request)
    assert request.url == "http://[Filtered]@127.0.0.1/path"


def test_sanitize_serialized_request():
    request = SerializedRequest(
        method="POST", uri="http://user:pass@127.0.0.1/path", body=None, body_size=None, headers={}
    )
    sanitize_request(request)
    assert request.uri == "http://[Filtered]@127.0.0.1/path"


def test_sanitize_output(case_factory, request_factory):
    response = Response()
    response.headers = {"API-Key": "secret"}
    response.request = request_factory(headers={"Custom-Token": "custom_token_value"})
    case = case_factory(headers={"Authorization": "Bearer token"}, query={"api_key": "12345"})
    sanitize_output(case, response=response)
    assert case.headers == {"Authorization": "[Filtered]"}
    assert case.query == {"api_key": "[Filtered]"}
    assert response.headers == {"API-Key": "[Filtered]"}
    assert response.request.headers == {"Custom-Token": "[Filtered]", "Content-Length": "0"}


def test_sanitize_output_no_response(case_factory):
    case = case_factory(headers={"Authorization": "Bearer token"}, query={"api_key": "12345"})
    sanitize_output(case)
    assert case.headers == {"Authorization": "[Filtered]"}
    assert case.query == {"api_key": "[Filtered]"}


URLENCODED_REPLACEMENT = urlencode({"": DEFAULT_REPLACEMENT})[1:]  # skip the `=` character


@pytest.mark.parametrize(
    "input_url, expected_url",
    [
        # No sensitive data
        (
            "http://127.0.0.1/path?param1=value1&param2=value2",
            "http://127.0.0.1/path?param1=value1&param2=value2",
        ),
        # Masking authority
        (
            "http://user:pass@127.0.0.1/path",
            f"http://{DEFAULT_REPLACEMENT}@127.0.0.1/path",
        ),
        # Masking query parameters with default keys
        (
            "http://127.0.0.1/path?password=secret&token=abc123",
            f"http://127.0.0.1/path?password={URLENCODED_REPLACEMENT}&token={URLENCODED_REPLACEMENT}",
        ),
        # Masking both authority and query parameters
        (
            "http://user:pass@127.0.0.1/path?password=secret&token=abc123",
            f"http://{DEFAULT_REPLACEMENT}@127.0.0.1/path?password={URLENCODED_REPLACEMENT}&token={URLENCODED_REPLACEMENT}",
        ),
        # URL with fragment
        (
            "http://127.0.0.1/path?password=secret#fragment",
            f"http://127.0.0.1/path?password={URLENCODED_REPLACEMENT}#fragment",
        ),
        # URL with port
        (
            "http://127.0.0.1:8080/path?password=secret",
            f"http://127.0.0.1:8080/path?password={URLENCODED_REPLACEMENT}",
        ),
        # URL with special characters in query params
        (
            "http://127.0.0.1/path?password=secret%20password",
            f"http://127.0.0.1/path?password={URLENCODED_REPLACEMENT}",
        ),
        # No query parameters
        (
            "http://127.0.0.1/path",
            "http://127.0.0.1/path",
        ),
    ],
)
def test_sanitize_url(input_url, expected_url):
    assert sanitize_url(input_url) == expected_url


@pytest.fixture
def serialized_check(case_factory, response_factory):
    root_case = case_factory()
    value = "secret"
    root_case.source = CaseSource(
        case=case_factory(),
        response=response_factory.requests(headers={"X-Token": value}),
        elapsed=1.0,
        overrides_all_parameters=True,
        transition_id=TransitionId(name="CustomLink", status_code="201"),
    )
    check = Check(
        name="test",
        value=Status.failure,
        response=response_factory.requests(headers={"X-Token": value}),
        elapsed=1.0,
        example=root_case,
    )
    return SerializedCheck.from_check(check)


def test_sanitize_serialized_check(serialized_check):
    sanitize_serialized_check(serialized_check)
    assert serialized_check.example.extra_headers["X-Token"] == DEFAULT_REPLACEMENT
    assert serialized_check.history[0].case.extra_headers["X-Token"] == DEFAULT_REPLACEMENT


def test_sanitize_serialized_interaction(serialized_check):
    request = SerializedRequest(
        method="POST", uri="http://user:pass@127.0.0.1/path", body=None, body_size=None, headers={"X-Token": "Secret"}
    )
    response = SerializedResponse(
        status_code=500,
        message="Internal Server Error",
        body=None,
        body_size=None,
        headers={"X-Token": ["Secret"]},
        encoding=None,
        http_version="1.1",
        elapsed=1.0,
        verify=True,
    )
    interaction = SerializedInteraction(
        request=request,
        response=response,
        checks=[serialized_check],
        status=Status.failure,
        recorded_at="",
        data_generation_method=DataGenerationMethod.positive,
        phase=TestPhase.GENERATE,
    )
    sanitize_serialized_interaction(interaction)

    assert interaction.checks[0].example.extra_headers["X-Token"] == DEFAULT_REPLACEMENT
    assert interaction.checks[0].history[0].case.extra_headers["X-Token"] == DEFAULT_REPLACEMENT
    assert interaction.request.uri == f"http://{DEFAULT_REPLACEMENT}@127.0.0.1/path"
    assert interaction.request.headers["X-Token"] == DEFAULT_REPLACEMENT
    assert interaction.response.headers["X-Token"] == [DEFAULT_REPLACEMENT]


@pytest.fixture
def config():
    return Config()


def test_with_keys_to_sanitize(config):
    new_keys = {"new_key1", "new_key2"}
    updated_config = config.with_keys_to_sanitize(*new_keys)
    assert updated_config.keys_to_sanitize == DEFAULT_KEYS_TO_SANITIZE.union(new_keys)


def test_without_keys_to_sanitize(config):
    remove_keys = {"phpsessid", "xsrf-token"}
    updated_config = config.without_keys_to_sanitize(*remove_keys)
    assert updated_config.keys_to_sanitize == DEFAULT_KEYS_TO_SANITIZE.difference(remove_keys)


def test_with_sensitive_markers(config):
    new_markers = {"new_marker1", "new_marker2"}
    updated_config = config.with_sensitive_markers(*new_markers)
    assert updated_config.sensitive_markers == DEFAULT_SENSITIVE_MARKERS.union(new_markers)


def test_without_sensitive_markers(config):
    remove_markers = {"token", "key"}
    updated_config = config.without_sensitive_markers(*remove_markers)
    assert updated_config.sensitive_markers == DEFAULT_SENSITIVE_MARKERS.difference(remove_markers)


def test_default_replacement_unchanged(config):
    new_keys = {"new_key1", "new_key2"}
    updated_config = config.with_keys_to_sanitize(*new_keys)
    assert updated_config.replacement == DEFAULT_REPLACEMENT


@pytest.mark.parametrize("header", ("x-customer-id", "X-CUSTOMER-ID", "X-Customer-Id"))
def test_configure_keys_to_sanitize(case_factory, header):
    configure(Config().with_keys_to_sanitize("X-Customer-ID"))
    case = case_factory(headers={header: "sensitive"})
    sanitize_case(case)
    assert case.headers == {header: "[Filtered]"}


@pytest.mark.parametrize("header", ("billing-address", "BILLING-ADDRESS", "Billing-Address"))
def test_configure_sensitive_markers(case_factory, header):
    configure(Config().with_sensitive_markers("billing"))
    case = case_factory(headers={header: "sensitive"})
    sanitize_case(case)
    assert case.headers == {header: "[Filtered]"}
