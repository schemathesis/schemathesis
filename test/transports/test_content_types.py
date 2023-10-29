import pytest

from schemathesis.transports.content_types import is_json_media_type, is_plain_text_media_type, parse_content_type


@pytest.mark.parametrize(
    "value, expected",
    (
        ("text/plain", ("text", "plain")),
        ("application/problem+json", ("application", "problem+json")),
        ("application/json;charset=utf-8", ("application", "json")),
        ("application/json/random", ("application", "json/random")),
    ),
)
def test_parse_content_type(value, expected):
    assert parse_content_type(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    (
        ("application/problem+json", True),
        ("application/json", True),
        ("application/xml", False),
        ("text/plain", False),
    ),
)
def test_is_json_media_type(value, expected):
    assert is_json_media_type(value) is expected


@pytest.mark.parametrize(
    "value, expected",
    (
        ("text/plain", True),
        ("text/plain;charset=utf-8", True),
        ("application/json", False),
        ("application/problem+json", False),
    ),
)
def test_is_plain_text_media_type(value, expected):
    assert is_plain_text_media_type(value) is expected
