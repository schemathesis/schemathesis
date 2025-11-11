from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from schemathesis.core.errors import MalformedMediaType

YAML_MEDIA_TYPES: tuple[str, ...] = (
    "text/yaml",
    "text/x-yaml",
    "application/x-yaml",
    "text/vnd.yaml",
    "application/yaml",
)


def _parseparam(s: str) -> Generator[str]:
    while s[:1] == ";":
        s = s[1:]
        end = s.find(";")
        while end > 0 and (s.count('"', 0, end) - s.count('\\"', 0, end)) % 2:
            end = s.find(";", end + 1)
        if end < 0:
            end = len(s)
        f = s[:end]
        yield f.strip()
        s = s[end:]


def _parse_header(line: str) -> tuple[str, dict]:
    parts = _parseparam(";" + line)
    key = parts.__next__()
    pdict = {}
    for p in parts:
        i = p.find("=")
        if i >= 0:
            name = p[:i].strip().lower()
            value = p[i + 1 :].strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
                value = value.replace("\\\\", "\\").replace('\\"', '"')
            pdict[name] = value
    return key, pdict


@lru_cache
def parse(media_type: str) -> tuple[str, str]:
    """Parse Content Type and return main type and subtype."""
    try:
        media_type, _ = _parse_header(media_type)
        main_type, sub_type = media_type.split("/", 1)
    except ValueError as exc:
        raise MalformedMediaType(f"Malformed media type: `{media_type}`") from exc
    return main_type.lower(), sub_type.lower()


def is_json(value: str) -> bool:
    """Detect whether the content type is JSON-compatible.

    For example - ``application/problem+json`` matches.
    """
    main, sub = parse(value)
    return main == "application" and (sub == "json" or sub.endswith("+json"))


def is_yaml(value: str) -> bool:
    """Detect whether the content type is YAML-compatible."""
    return value in YAML_MEDIA_TYPES


def is_plain_text(value: str) -> bool:
    """Detect variations of the ``text/plain`` media type."""
    return parse(value) == ("text", "plain")


def is_xml(value: str) -> bool:
    """Detect variations of the ``application/xml`` media type."""
    _, sub = parse(value)
    return sub == "xml" or sub.endswith("+xml")


def matches_parts(expected: tuple[str, str], actual: tuple[str, str]) -> bool:
    """Check if two parsed media types match with wildcard support."""
    expected_main, expected_sub = expected
    actual_main, actual_sub = actual
    main_matches = expected_main == "*" or expected_main == actual_main
    sub_matches = expected_sub == "*" or expected_sub == actual_sub
    return main_matches and sub_matches


def matches(expected: str, actual: str) -> bool:
    """Check if two media type strings match with wildcard support."""
    return matches_parts(parse(expected), parse(actual))
