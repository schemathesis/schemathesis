from dataclasses import replace
from urllib.parse import urlencode

import pytest

from schemathesis.core import NOT_SET
from schemathesis.core.output.sanitization import (
    DEFAULT_KEYS_TO_SANITIZE,
    DEFAULT_REPLACEMENT,
    SanitizationConfig,
    sanitize_url,
    sanitize_value,
)


@pytest.fixture
def sanitized_case_factory(case_factory):
    def factory(keys_to_sanitize=DEFAULT_KEYS_TO_SANITIZE, default_replacement=DEFAULT_REPLACEMENT, **kwargs):
        case = case_factory(**kwargs)
        config = SanitizationConfig(keys_to_sanitize=keys_to_sanitize, replacement=default_replacement)
        if case.path_parameters is not None:
            sanitize_value(case.path_parameters, config=config)
        if case.headers is not None:
            sanitize_value(case.headers, config=config)
        if case.cookies is not None:
            sanitize_value(case.cookies, config=config)
        if case.query is not None:
            sanitize_value(case.query, config=config)
        if case.body not in (None, NOT_SET):
            sanitize_value(case.body, config=config)
        return case

    return factory


@pytest.mark.parametrize(
    ("attr", "initial", "expected"),
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
def test_sanitize_case(sanitized_case_factory, attr, initial, expected):
    case = sanitized_case_factory(**{attr: initial})
    assert getattr(case, attr) == expected


def test_sanitize_case_body_not_dict_or_not_set(sanitized_case_factory):
    assert sanitized_case_factory(body="Some string body").body == "Some string body"  # Body should remain unchanged


def test_sanitize_case_body_is_not_set(sanitized_case_factory):
    assert sanitized_case_factory(body=NOT_SET).body is NOT_SET  # Body should remain unchanged


def test_sanitize_case_custom_keys_to_sanitize(sanitized_case_factory):
    case = sanitized_case_factory(query={"custom_key": "sensitive"}, keys_to_sanitize=("custom_key",))
    assert case.query["custom_key"] == "[Filtered]"


def test_sanitize_case_custom_replacement(sanitized_case_factory):
    custom_replacement = "[Redacted]"
    case = sanitized_case_factory(path_parameters={"password": "1234"}, default_replacement=custom_replacement)
    assert case.path_parameters["password"] == custom_replacement


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"nested": {"secret": "reveal"}, "foo": 123}, {"nested": {"secret": "[Filtered]"}, "foo": 123}),
        ([{"secret": "reveal"}, 1], [{"secret": "[Filtered]"}, 1]),
        ("string body", "string body"),
        (123, 123),
        (NOT_SET, NOT_SET),
    ],
)
def test_sanitize_case_body_variants(sanitized_case_factory, body, expected):
    assert sanitized_case_factory(body=body).body == expected


URLENCODED_REPLACEMENT = urlencode({"": DEFAULT_REPLACEMENT})[1:]  # skip the `=` character


@pytest.mark.parametrize(
    ("input_url", "expected_url"),
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


@pytest.mark.parametrize(
    "kwargs, expected_changes",
    [
        (
            {"replacement": "[HIDDEN]"},
            {"replacement": "[HIDDEN]"},
        ),
        (
            {"keys_to_sanitize": ["New-Key"]},
            {"keys_to_sanitize": frozenset(["new-key"])},
        ),
        (
            {"sensitive_markers": ["New-Marker"]},
            {"sensitive_markers": frozenset(["new-marker"])},
        ),
        (
            {
                "replacement": "[SECRET]",
                "keys_to_sanitize": ["Key1"],
                "sensitive_markers": ["Marker1"],
            },
            {
                "replacement": "[SECRET]",
                "keys_to_sanitize": frozenset(["key1"]),
                "sensitive_markers": frozenset(["marker1"]),
            },
        ),
    ],
)
def test_from_config(kwargs, expected_changes):
    config = SanitizationConfig()
    assert SanitizationConfig.from_config(config, **kwargs) == replace(config, **expected_changes)


@pytest.mark.parametrize(
    "base_values, extend_values, expected",
    [
        (
            {"keys_to_sanitize": frozenset(["existing-key"])},
            {"keys_to_sanitize": ["new-key"]},
            {"keys_to_sanitize": frozenset(["existing-key", "new-key"])},
        ),
        (
            {"sensitive_markers": frozenset(["existing-marker"])},
            {"sensitive_markers": ["new-marker"]},
            {"sensitive_markers": frozenset(["existing-marker", "new-marker"])},
        ),
        (
            {
                "keys_to_sanitize": frozenset(["existing-key"]),
                "sensitive_markers": frozenset(["existing-marker"]),
            },
            {
                "keys_to_sanitize": ["NEW-KEY"],
                "sensitive_markers": ["NEW-MARKER"],
            },
            {
                "keys_to_sanitize": frozenset(["existing-key", "new-key"]),
                "sensitive_markers": frozenset(["existing-marker", "new-marker"]),
            },
        ),
    ],
)
def test_extend(base_values, extend_values, expected):
    base = SanitizationConfig(**base_values)
    new = base.extend(**extend_values)
    expected = replace(base, **expected)
    assert new == expected
