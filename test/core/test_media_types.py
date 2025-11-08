import pytest

from schemathesis.core import media_types
from schemathesis.core.errors import MalformedMediaType


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("text/plain", ("text", "plain")),
        ("application/problem+json", ("application", "problem+json")),
        ("application/json;charset=utf-8", ("application", "json")),
        ("application/json/random", ("application", "json/random")),
        ('text/plain; boundary="----; quoted"', ("text", "plain")),
        ('application/json; param="value\\\\with\\\\backslash"', ("application", "json")),
        ('text/html; charset="utf-8"; boundary="test"', ("text", "html")),
        ("text/plain; flagparam", ("text", "plain")),
    ],
)
def test_parse_content_type(value, expected):
    assert media_types.parse(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "invalid",  # No slash
        "justtext",  # No slash
        "",  # Empty string
    ],
)
def test_parse_content_type_malformed(value):
    with pytest.raises(MalformedMediaType, match="Malformed media type"):
        media_types.parse(value)


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("text/yaml", True),
        ("text/x-yaml", True),
        ("application/x-yaml", True),
        ("text/vnd.yaml", True),
        ("application/yaml", True),
        ("application/json", False),
        ("text/plain", False),
        ("application/xml", False),
    ],
)
def test_is_yaml_media_type(value, expected):
    assert media_types.is_yaml(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("application/xml", True),
        ("text/xml", True),
        ("application/xhtml+xml", True),
        ("application/atom+xml", True),
        ("application/rss+xml", True),
        ("application/json", False),
        ("text/plain", False),
        ("application/yaml", False),
    ],
)
def test_is_xml_media_type(value, expected):
    assert media_types.is_xml(value) is expected


@pytest.mark.parametrize(
    ("expected", "actual", "should_match"),
    [
        # Exact matches
        ("application/json", "application/json", True),
        ("text/plain", "text/plain", True),
        ("application/problem+json", "application/problem+json", True),
        # Different media types
        ("application/json", "application/xml", False),
        ("text/plain", "text/html", False),
        ("application/json", "text/plain", False),
        # Wildcard main type
        ("*/json", "application/json", True),
        ("*/json", "text/json", True),
        ("*/xml", "application/json", False),
        # Wildcard subtype
        ("application/*", "application/json", True),
        ("application/*", "application/xml", True),
        ("application/*", "text/plain", False),
        ("text/*", "text/plain", True),
        ("text/*", "application/json", False),
        # Full wildcard
        ("*/*", "application/json", True),
        ("*/*", "text/plain", True),
        ("*/*", "image/png", True),
        # Parameters should not affect matching (handled by parse)
        ("application/json", "application/json;charset=utf-8", True),
        # Complex subtypes
        ("application/problem+json", "application/problem+json", True),
        ("application/vnd.api+json", "application/vnd.api+json", True),
        ("application/*", "application/problem+json", True),
    ],
)
def test_matches(expected, actual, should_match):
    assert media_types.matches(expected, actual) == should_match


@pytest.mark.parametrize(
    ("expected", "actual", "should_match"),
    [
        # Exact matches
        (("application", "json"), ("application", "json"), True),
        (("text", "plain"), ("text", "plain"), True),
        # Different media types
        (("application", "json"), ("application", "xml"), False),
        (("text", "plain"), ("application", "json"), False),
        # Wildcard main type
        (("*", "json"), ("application", "json"), True),
        (("*", "json"), ("text", "json"), True),
        (("*", "xml"), ("application", "json"), False),
        # Wildcard subtype
        (("application", "*"), ("application", "json"), True),
        (("application", "*"), ("application", "xml"), True),
        (("application", "*"), ("text", "plain"), False),
        # Full wildcard
        (("*", "*"), ("application", "json"), True),
        (("*", "*"), ("text", "plain"), True),
        # Asymmetry: wildcard only in expected
        (("application", "*"), ("application", "json"), True),
        (("application", "json"), ("application", "*"), False),
    ],
)
def test_matches_parts(expected, actual, should_match):
    assert media_types.matches_parts(expected, actual) == should_match
