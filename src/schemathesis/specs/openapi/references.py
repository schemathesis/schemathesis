from __future__ import annotations

import sys
from collections.abc import Callable
from functools import lru_cache
from typing import Any
from urllib.request import urlopen

import requests

from schemathesis.core.compat import RefResolutionError, RefResolver
from schemathesis.core.deserialization import deserialize_yaml
from schemathesis.core.errors import RemoteDocumentError
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT


def load_file_impl(location: str, opener: Callable) -> dict[str, Any]:
    """Load a schema from the given file."""
    with opener(location) as fd:
        return deserialize_yaml(fd)


@lru_cache
def load_file(location: str) -> dict[str, Any]:
    """Load a schema from the given file."""
    return load_file_impl(location, open)


@lru_cache
def load_file_uri(location: str) -> dict[str, Any]:
    """Load a schema from the given file uri."""
    return load_file_impl(location, urlopen)


_HTML_MARKERS = (b"<!doctype", b"<html", b"<head", b"<body")


def _looks_like_html(content_type: str | None, body: bytes) -> bool:
    if content_type and "html" in content_type.lower():
        return True
    head = body.lstrip()[:64].lower()
    return any(head.startswith(m) for m in _HTML_MARKERS)


def load_remote_uri(uri: str) -> Any:
    """Load the resource and parse it as YAML / JSON."""
    response = requests.get(uri, timeout=DEFAULT_RESPONSE_TIMEOUT)
    content_type = response.headers.get("Content-Type", "")
    body = response.content or b""

    def _suffix() -> str:
        return f"(HTTP {response.status_code}, Content-Type={content_type}, size={len(body)})"

    if not (200 <= response.status_code < 300):
        raise RemoteDocumentError(f"Failed to fetch {_suffix()}")

    if _looks_like_html(content_type, body):
        raise RemoteDocumentError(f"Expected YAML/JSON, got HTML {_suffix()}")

    document = deserialize_yaml(response.content)

    if not isinstance(document, (dict, list)):
        raise RemoteDocumentError(
            f"Remote document is parsed as {type(document).__name__}, but an object/array is expected {_suffix()}"
        )

    return document


JSONType = None | bool | float | str | list | dict[str, Any]


class ReferenceResolver(RefResolver):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault(
            "handlers", {"file": load_file_uri, "": load_file, "http": load_remote_uri, "https": load_remote_uri}
        )
        super().__init__(*args, **kwargs)

    if sys.version_info >= (3, 11):

        def resolve(self, ref: str) -> tuple[str, Any]:
            try:
                return super().resolve(ref)
            except RefResolutionError as exc:
                exc.add_note(ref)
                raise
    else:

        def resolve(self, ref: str) -> tuple[str, Any]:
            try:
                return super().resolve(ref)
            except RefResolutionError as exc:
                exc.__notes__ = [ref]
                raise
