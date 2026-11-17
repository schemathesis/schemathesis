from urllib.parse import urlencode

import pytest

from schemathesis.config import SanitizationConfig
from schemathesis.config._output import DEFAULT_KEYS_TO_SANITIZE, DEFAULT_REPLACEMENT
from schemathesis.core import NOT_SET
from schemathesis.core.output.sanitization import sanitize_headers, sanitize_url, sanitize_value


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
    assert sanitized_case_factory(body="Some string body").body == "Some string body"


def test_sanitize_case_body_is_not_set(sanitized_case_factory):
    assert sanitized_case_factory(body=NOT_SET).body is NOT_SET


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
        (
            "http://127.0.0.1/path?param1=value1&param2=value2",
            "http://127.0.0.1/path?param1=value1&param2=value2",
        ),
        (
            "http://user:pass@127.0.0.1/path",
            f"http://{DEFAULT_REPLACEMENT}@127.0.0.1/path",
        ),
        (
            "http://127.0.0.1/path?password=secret&token=abc123",
            f"http://127.0.0.1/path?password={URLENCODED_REPLACEMENT}&token={URLENCODED_REPLACEMENT}",
        ),
        (
            "http://user:pass@127.0.0.1/path?password=secret&token=abc123",
            f"http://{DEFAULT_REPLACEMENT}@127.0.0.1/path?password={URLENCODED_REPLACEMENT}&token={URLENCODED_REPLACEMENT}",
        ),
        (
            "http://127.0.0.1/path?password=secret#fragment",
            f"http://127.0.0.1/path?password={URLENCODED_REPLACEMENT}#fragment",
        ),
        (
            "http://127.0.0.1:8080/path?password=secret",
            f"http://127.0.0.1:8080/path?password={URLENCODED_REPLACEMENT}",
        ),
        (
            "http://127.0.0.1/path?password=secret%20password",
            f"http://127.0.0.1/path?password={URLENCODED_REPLACEMENT}",
        ),
        (
            "http://127.0.0.1/path",
            "http://127.0.0.1/path",
        ),
    ],
)
def test_sanitize_url(input_url, expected_url):
    assert sanitize_url(input_url, config=SanitizationConfig()) == expected_url


@pytest.mark.parametrize(
    ("input_url", "expected_url"),
    [
        ("http://127.0.0.1/path?a=1&b=2&a=3", "http://127.0.0.1/path?a=1&b=2&a=3"),
        ("http://127.0.0.1/path?flag", "http://127.0.0.1/path?flag"),
        ("http://127.0.0.1/path?q=%FF", "http://127.0.0.1/path?q=%FF"),
        ("http://127.0.0.1/path?q=a+b%20c", "http://127.0.0.1/path?q=a+b%20c"),
        (
            "http://127.0.0.1/path?a=1&token=secret&b=%FF&flag&a=3",
            f"http://127.0.0.1/path?a=1&token={URLENCODED_REPLACEMENT}&b=%FF&flag&a=3",
        ),
        ("http://127.0.0.1/path?a=1&token", "http://127.0.0.1/path?a=1&token"),
    ],
    ids=[
        "repeated-keys",
        "bare-flag",
        "invalid-utf8",
        "mixed-space-encoding",
        "sensitive-among-others",
        "bare-sensitive-key",
    ],
)
def test_sanitize_url_preserves_query(input_url, expected_url):
    assert sanitize_url(input_url, config=SanitizationConfig()) == expected_url


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Cookie": "token=a=b; lang=en"}, {"Cookie": "token=[Filtered]; lang=en"}),
        ({"cookie": ["sessionid=abc", "lang=en"]}, {"cookie": ["sessionid=[Filtered]", "lang=en"]}),
        ({"Cookie": " sessionid = abc ;lang=en;"}, {"Cookie": " sessionid =[Filtered];lang=en;"}),
        ({"Cookie": ""}, {"Cookie": ""}),
        ({"Cookie": 42}, {"Cookie": "[Filtered]"}),
        (
            {"Set-Cookie": ["sessionid=xyz; Path=/; HttpOnly", "lang=en; Path=/"]},
            {"Set-Cookie": ["sessionid=[Filtered]; Path=/; HttpOnly", "lang=en; Path=/"]},
        ),
        (
            {"set-cookie": "sessionid=xyz; Expires=Wed, 21 Oct 2026 07:28:00 GMT; Path=/"},
            {"set-cookie": "sessionid=[Filtered]; Expires=Wed, 21 Oct 2026 07:28:00 GMT; Path=/"},
        ),
        ({"Set-Cookie": "lang=en"}, {"Set-Cookie": "lang=en"}),
        ({"Set-Cookie": "lang=en; Path=/, sessionid=xyz; Path=/"}, {"Set-Cookie": "[Filtered]"}),
        ({"Set-Cookie": "sessionid; Path=/"}, {"Set-Cookie": "[Filtered]"}),
        (
            {"Authorization": ["Bearer a", "Bearer b"], "X-Trace": "1"},
            {"Authorization": ["[Filtered]"], "X-Trace": "1"},
        ),
    ],
    ids=[
        "equals-in-value",
        "repeated-cookie-headers",
        "whitespace-and-trailing-separator",
        "empty",
        "non-string",
        "set-cookie-attributes",
        "set-cookie-expires",
        "set-cookie-without-attributes",
        "set-cookie-joined",
        "set-cookie-without-value",
        "other-headers",
    ],
)
def test_sanitize_headers(headers, expected):
    sanitize_headers(headers, config=SanitizationConfig())
    assert headers == expected
