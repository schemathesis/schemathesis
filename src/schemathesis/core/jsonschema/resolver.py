from __future__ import annotations

import sys
from collections.abc import Callable
from functools import lru_cache
from typing import Any
from urllib.parse import urldefrag, urljoin
from urllib.request import urlopen

import jsonschema_rs
import requests

from schemathesis.core.compat import RefResolutionError
from schemathesis.core.deserialization import deserialize_yaml
from schemathesis.core.errors import RemoteDocumentError
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT

IN_MEMORY_BASE_URI = "urn:schemathesis:root"


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
    """Load a schema from the given file URI."""
    return load_file_impl(location, urlopen)


_HTML_MARKERS = (b"<!doctype", b"<html", b"<head", b"<body")


def _looks_like_html(content_type: str | None, body: bytes) -> bool:
    if content_type and "html" in content_type.lower():
        return True
    head = body.lstrip()[:64].lower()
    return any(head.startswith(marker) for marker in _HTML_MARKERS)


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

    if not isinstance(document, dict | list):
        raise RemoteDocumentError(
            f"Remote document is parsed as {type(document).__name__}, but an object/array is expected {_suffix()}"
        )

    return document


def retrieve(uri: str) -> Any:
    if uri.startswith("file://"):
        return load_file_uri(uri)
    if uri.startswith(("http://", "https://")):
        return load_remote_uri(uri)
    return load_file(uri)


def build_registry(
    root_schema: dict[str, Any],
    *,
    location: str | None = None,
    draft: int | None = None,
) -> jsonschema_rs.Registry:
    base_uri = location or IN_MEMORY_BASE_URI
    return jsonschema_rs.Registry([(base_uri, root_schema)], draft=draft, retriever=retrieve)


def make_root_resolver(
    root_schema: dict[str, Any],
    *,
    location: str | None = None,
    draft: int | None = None,
) -> jsonschema_rs.Resolver:
    base_uri = location or IN_MEMORY_BASE_URI
    registry = build_registry(root_schema, location=base_uri, draft=draft)
    return registry.resolver(base_uri)


def resolve_reference_uri(base_uri: str, reference: str) -> str:
    if not reference.strip():
        return base_uri

    document_uri = urldefrag(base_uri)[0]
    if reference.startswith("#"):
        return f"{document_uri}{reference}"

    if reference.startswith(("http://", "https://", "file://", "urn:")):
        return reference

    if "://" in document_uri:
        return urljoin(document_uri, reference)

    if "/" not in document_uri:
        return reference

    path, _, fragment = reference.partition("#")
    resolved_path = f"{document_uri.rsplit('/', 1)[0]}/{path}"
    return f"{resolved_path}#{fragment}" if fragment else resolved_path


def resolve_reference(resolver: jsonschema_rs.Resolver, reference: str) -> tuple[jsonschema_rs.Resolver, Any]:
    try:
        resolved_uri = resolve_reference_uri(resolver.base_uri, reference)
        document_uri, _, fragment = resolved_uri.partition("#")
        current_document_uri = urldefrag(resolver.base_uri)[0]

        if document_uri and document_uri != current_document_uri:
            document = retrieve(document_uri)
            external_resolver = make_root_resolver(document, location=document_uri)
            if fragment:
                return resolve_reference(external_resolver, f"#{fragment}")
            return external_resolver, document

        resolved = resolver.lookup(reference)
    except (jsonschema_rs.ReferencingError, OSError, RemoteDocumentError, requests.RequestException) as exc:
        error = RefResolutionError(str(exc))
        if sys.version_info >= (3, 11):
            error.add_note(reference)
        else:
            error.__notes__ = [reference]
        raise error from exc
    return resolved.resolver, resolved.contents
