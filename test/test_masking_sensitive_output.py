from urllib.parse import urlencode

import pytest
import requests
from requests import Request, Response

from schemathesis.masking import (
    DEFAULT_KEYS_TO_MASK,
    DEFAULT_REPLACEMENT,
    DEFAULT_SENSITIVE_MARKERS,
    MaskingConfig,
    mask_case,
    mask_history,
    mask_request,
    mask_sensitive_output,
    mask_serialized_check,
    mask_serialized_interaction,
    mask_url,
)
from schemathesis.models import CaseSource, Check
from schemathesis.models import Request as SerializedRequest
from schemathesis.models import Response as SerializedResponse
from schemathesis.models import Status
from schemathesis.runner.serialization import SerializedCheck, SerializedInteraction
from schemathesis.utils import NOT_SET


@pytest.fixture
def request_factory():
    def factory(url="http://127.0.0.1", headers=None):
        request = Request(url=url, headers=headers or {})
        return request.prepare()

    return factory


@pytest.fixture
def masked_case(case_factory):
    def factory(keys_to_mask=DEFAULT_KEYS_TO_MASK, default_replacement=DEFAULT_REPLACEMENT, **kwargs):
        case = case_factory(**kwargs)
        config = MaskingConfig(keys_to_mask=keys_to_mask, replacement=default_replacement)
        mask_case(case, config=config)
        return case

    return factory


@pytest.mark.parametrize(
    "attr, initial, expected",
    [
        ("path_parameters", {"password": "1234"}, {"password": "[Masked]"}),
        ("headers", {"Authorization": "Bearer token"}, {"Authorization": "[Masked]"}),
        ("headers", {"Authorization": ["Bearer token"]}, {"Authorization": ["[Masked]"]}),
        ("headers", {"X-Foo-Authorization": "Bearer token"}, {"X-Foo-Authorization": "[Masked]"}),
        ("cookies", {"session": "xyz"}, {"session": "[Masked]"}),
        ("query", {"api_key": "5678"}, {"api_key": "[Masked]"}),
        ("body", {"nested": {"password": "password"}}, {"nested": {"password": "[Masked]"}}),
    ],
)
def test_mask_case(masked_case, attr, initial, expected):
    case = masked_case(**{attr: initial})
    assert getattr(case, attr) == expected


def test_mask_case_body_not_dict_or_not_set(masked_case):
    assert masked_case(body="Some string body").body == "Some string body"  # Body should remain unchanged


def test_mask_case_body_is_not_set(masked_case):
    assert masked_case(body=NOT_SET).body is NOT_SET  # Body should remain unchanged


def test_mask_case_custom_keys_to_mask(masked_case):
    case = masked_case(query={"custom_key": "sensitive"}, keys_to_mask=("custom_key",))
    assert case.query["custom_key"] == "[Masked]"


def test_mask_case_custom_replacement(masked_case):
    custom_replacement = "[Redacted]"
    case = masked_case(path_parameters={"password": "1234"}, default_replacement=custom_replacement)
    assert case.path_parameters["password"] == custom_replacement


@pytest.mark.parametrize(
    "body, expected",
    [
        ({"nested": {"secret": "reveal"}, "foo": 123}, {"nested": {"secret": "[Masked]"}, "foo": 123}),
        ([{"secret": "reveal"}, 1], [{"secret": "[Masked]"}, 1]),
        ("string body", "string body"),
        (123, 123),
        (NOT_SET, NOT_SET),
    ],
)
def test_mask_case_body_variants(masked_case, body, expected):
    assert masked_case(body=body).body == expected


def test_mask_history(case_factory):
    case3 = case_factory(headers={"Authorization": "Bearer token"})
    source3 = CaseSource(case=case3, response=requests.Response(), elapsed=0.3)

    case2 = case_factory(headers={"X-API-Key": "12345"}, source=source3)
    source2 = CaseSource(case=case2, response=requests.Response(), elapsed=0.2)

    case1 = case_factory(headers={"Password": "password"}, source=source2)
    source1 = CaseSource(case=case1, response=requests.Response(), elapsed=0.1)

    mask_history(source1)

    assert case1.headers == {"Password": "[Masked]"}
    assert case2.headers == {"X-API-Key": "[Masked]"}
    assert case3.headers == {"Authorization": "[Masked]"}


def test_mask_history_empty(case_factory):
    case = case_factory(headers={"Password": "password"})
    source = CaseSource(case=case, response=requests.Response(), elapsed=0.1)

    mask_history(source)

    assert case.headers == {"Password": "[Masked]"}


@pytest.mark.parametrize(
    "headers, expected",
    (
        ({"Authorization": "Bearer token"}, {"Authorization": "[Masked]"}),
        ({"Custom-Token": "custom_token_value"}, {"Custom-Token": "[Masked]"}),
        ({"Content-Type": "application/json"}, {"Content-Type": "application/json"}),
    ),
)
def test_mask_request(request_factory, headers, expected):
    request = request_factory(headers=headers)
    mask_request(request)
    assert request.headers == {**expected, "Content-Length": "0"}


def test_mask_request_url(request_factory):
    request = request_factory(url="http://user:pass@127.0.0.1/path")
    mask_request(request)
    assert request.url == "http://[Masked]@127.0.0.1/path"


def test_mask_serialized_request():
    request = SerializedRequest(method="POST", uri="http://user:pass@127.0.0.1/path", body=None, headers={})
    mask_request(request)
    assert request.uri == "http://[Masked]@127.0.0.1/path"


def test_mask_sensitive_output(case_factory, request_factory):
    response = Response()
    response.headers = {"API-Key": "secret"}
    response.request = request_factory(headers={"Custom-Token": "custom_token_value"})
    case = case_factory(headers={"Authorization": "Bearer token"}, query={"api_key": "12345"})
    mask_sensitive_output(case, response=response)
    assert case.headers == {"Authorization": "[Masked]"}
    assert case.query == {"api_key": "[Masked]"}
    assert response.headers == {"API-Key": "[Masked]"}
    assert response.request.headers == {"Custom-Token": "[Masked]", "Content-Length": "0"}


def test_mask_sensitive_output_no_response(case_factory):
    case = case_factory(headers={"Authorization": "Bearer token"}, query={"api_key": "12345"})
    mask_sensitive_output(case)
    assert case.headers == {"Authorization": "[Masked]"}
    assert case.query == {"api_key": "[Masked]"}


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
def test_mask_url(input_url, expected_url):
    assert mask_url(input_url) == expected_url


@pytest.fixture
def serialized_check(case_factory, response_factory):
    root_case = case_factory()
    value = "secret"
    root_case.source = CaseSource(
        case=case_factory(), response=response_factory.requests(headers={"X-Token": value}), elapsed=1.0
    )
    check = Check(
        name="test",
        value=Status.failure,
        response=response_factory.requests(headers={"X-Token": value}),
        elapsed=1.0,
        example=root_case,
    )
    return SerializedCheck.from_check(check)


def test_mask_serialized_check(serialized_check):
    mask_serialized_check(serialized_check)
    assert serialized_check.example.extra_headers["X-Token"] == DEFAULT_REPLACEMENT
    assert serialized_check.history[0].case.extra_headers["X-Token"] == DEFAULT_REPLACEMENT


def test_mask_serialized_interaction(serialized_check):
    request = SerializedRequest(
        method="POST", uri="http://user:pass@127.0.0.1/path", body=None, headers={"X-Token": "Secret"}
    )
    response = SerializedResponse(
        status_code=500,
        message="Internal Server Error",
        body=None,
        headers={"X-Token": ["Secret"]},
        encoding=None,
        http_version="1.1",
        elapsed=1.0,
        verify=True,
    )
    interaction = SerializedInteraction(
        request=request, response=response, checks=[serialized_check], status=Status.failure, recorded_at=""
    )
    mask_serialized_interaction(interaction)

    assert interaction.checks[0].example.extra_headers["X-Token"] == DEFAULT_REPLACEMENT
    assert interaction.checks[0].history[0].case.extra_headers["X-Token"] == DEFAULT_REPLACEMENT
    assert interaction.request.uri == f"http://{DEFAULT_REPLACEMENT}@127.0.0.1/path"
    assert interaction.request.headers["X-Token"] == DEFAULT_REPLACEMENT
    assert interaction.response.headers["X-Token"] == [DEFAULT_REPLACEMENT]


@pytest.fixture
def masking_config():
    return MaskingConfig()


def test_with_keys_to_mask(masking_config):
    new_keys = {"new_key1", "new_key2"}
    updated_config = masking_config.with_keys_to_mask(*new_keys)
    assert updated_config.keys_to_mask == DEFAULT_KEYS_TO_MASK.union(new_keys)


def test_without_keys_to_mask(masking_config):
    remove_keys = {"phpsessid", "xsrf-token"}
    updated_config = masking_config.without_keys_to_mask(*remove_keys)
    assert updated_config.keys_to_mask == DEFAULT_KEYS_TO_MASK.difference(remove_keys)


def test_with_sensitive_markers(masking_config):
    new_markers = {"new_marker1", "new_marker2"}
    updated_config = masking_config.with_sensitive_markers(*new_markers)
    assert updated_config.sensitive_markers == DEFAULT_SENSITIVE_MARKERS.union(new_markers)


def test_without_sensitive_markers(masking_config):
    remove_markers = {"token", "key"}
    updated_config = masking_config.without_sensitive_markers(*remove_markers)
    assert updated_config.sensitive_markers == DEFAULT_SENSITIVE_MARKERS.difference(remove_markers)


def test_default_replacement_unchanged(masking_config):
    new_keys = {"new_key1", "new_key2"}
    updated_config = masking_config.with_keys_to_mask(*new_keys)
    assert updated_config.replacement == DEFAULT_REPLACEMENT
