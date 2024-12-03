import pytest

from schemathesis.core import media_types


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("text/plain", ("text", "plain")),
        ("application/problem+json", ("application", "problem+json")),
        ("application/json;charset=utf-8", ("application", "json")),
        ("application/json/random", ("application", "json/random")),
    ],
)
def test_parse_content_type(value, expected):
    assert media_types.parse(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("application/problem+json", True),
        ("application/json", True),
        ("application/xml", False),
        ("text/plain", False),
    ],
)
def test_is_json_media_type(value, expected):
    assert media_types.is_json(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("text/plain", True),
        ("text/plain;charset=utf-8", True),
        ("application/json", False),
        ("application/problem+json", False),
    ],
)
def test_is_plain_text_media_type(value, expected):
    assert media_types.is_plain_text(value) is expected
