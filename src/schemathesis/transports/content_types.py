from functools import lru_cache
from typing import Generator, Tuple


def _parseparam(s: str) -> Generator[str, None, None]:
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


def parse_header(line: str) -> Tuple[str, dict]:
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


@lru_cache()
def parse_content_type(content_type: str) -> Tuple[str, str]:
    """Parse Content Type and return main type and subtype."""
    try:
        content_type, _ = parse_header(content_type)
        main_type, sub_type = content_type.split("/", 1)
    except ValueError as exc:
        raise ValueError(f"Malformed media type: `{content_type}`") from exc
    return main_type.lower(), sub_type.lower()


def is_json_media_type(value: str) -> bool:
    """Detect whether the content type is JSON-compatible.

    For example - ``application/problem+json`` matches.
    """
    main, sub = parse_content_type(value)
    return main == "application" and (sub == "json" or sub.endswith("+json"))


def is_yaml_media_type(value: str) -> bool:
    """Detect whether the content type is YAML-compatible."""
    return value in ("text/yaml", "text/x-yaml", "application/x-yaml", "text/vnd.yaml")


def is_plain_text_media_type(value: str) -> bool:
    """Detect variations of the ``text/plain`` media type."""
    return parse_content_type(value) == ("text", "plain")


def is_xml_media_type(value: str) -> bool:
    """Detect variations of the ``application/xml`` media type."""
    _, sub = parse_content_type(value)
    return sub == "xml" or sub.endswith("+xml")
