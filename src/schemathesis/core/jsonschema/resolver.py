from __future__ import annotations

import os
import sys
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin
from urllib.request import urlopen

import jsonschema_rs
import requests

from schemathesis.core.compat import RefResolutionError
from schemathesis.core.deserialization import deserialize_yaml
from schemathesis.core.errors import RemoteDocumentError
from schemathesis.core.transforms import UNRESOLVABLE, resolve_pointer
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT

IN_MEMORY_BASE_URI = "urn:schemathesis:root"

# Side table mapping a resolver's base URI to the original (un-cloned) root schema.
# `jsonschema_rs.Registry` deep-clones the schema with sorted dict keys, so we keep
# the original around to walk local fragments ourselves and preserve insertion order
# (which matters for HTTP method iteration, link ordering, etc.).
_ROOT_SCHEMAS: dict[str, Any] = {}
# Per-document JSON-pointer walk cache. Invalidated only when the root schema *object*
# for a base URI is replaced (see `make_root_resolver`); in-place mutations of the
# stored dict are not detected and would return stale entries.
_FRAGMENT_CACHES: dict[str, dict[str, Any]] = {}
_FRAGMENT_MISS = object()


def _normalize_location(location: str) -> str:
    """Convert a plain filesystem path to a `file://` URI so relative refs resolve via urljoin.

    Without this, `jsonschema_rs.Registry` rewrites plain paths to `json-schema:///...`,
    a scheme that `urljoin` does not treat as relative-aware.
    """
    if "://" in location or location.startswith("urn:"):
        return location
    return Path(os.path.abspath(location)).as_uri()


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


@lru_cache
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
    base_uri = _normalize_location(location) if location else IN_MEMORY_BASE_URI
    try:
        return jsonschema_rs.Registry([(base_uri, root_schema)], draft=draft, retriever=retrieve)
    except ValueError:
        # `jsonschema_rs.Registry` rejects non-string dict keys. YAML-loaded schemas can
        # produce bool keys (bare `on:`/`off:`/`yes:` get parsed as `True`/`False`),
        # which the previous Python resolver tolerated. Retry with stringified keys.
        return jsonschema_rs.Registry([(base_uri, _coerce_string_keys(root_schema))], draft=draft, retriever=retrieve)


def _coerce_string_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {(k if isinstance(k, str) else str(k)): _coerce_string_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce_string_keys(item) for item in value]
    return value


def make_root_resolver(
    root_schema: dict[str, Any],
    *,
    location: str | None = None,
    draft: int | None = None,
) -> jsonschema_rs.Resolver:
    base_uri = _normalize_location(location) if location else IN_MEMORY_BASE_URI
    registry = build_registry(root_schema, location=base_uri, draft=draft)
    if _ROOT_SCHEMAS.get(base_uri) is not root_schema:
        # Schema for this URI changed (or first registration) — drop stale fragment cache.
        _FRAGMENT_CACHES.pop(base_uri, None)
    _ROOT_SCHEMAS[base_uri] = root_schema
    return registry.resolver(base_uri)


@lru_cache(maxsize=4096)
def _resolve_reference_uri_with_document(base_uri: str, reference: str) -> tuple[str, str]:
    """Like `resolve_reference_uri`, but also returns the base document URI (without fragment).

    Cached because the bundler resolves the same `(base_uri, reference)` pair on every
    duplicate `$ref`.
    """
    document_uri = urldefrag(base_uri)[0]

    if not reference.strip():
        return base_uri, document_uri

    if reference.startswith("#"):
        return f"{document_uri}{reference}", document_uri

    if reference.startswith(("http://", "https://", "file://", "urn:")):
        return reference, document_uri

    if "://" in document_uri:
        return urljoin(document_uri, reference), document_uri

    # `_normalize_location` rewrites filesystem paths to `file://` URIs, so all base URIs
    # reaching here either match the `://` branch above or are `urn:`-prefixed. URNs have
    # no path component to resolve a relative reference against, so return it as-is.
    return reference, document_uri


def resolve_reference_uri(base_uri: str, reference: str) -> str:
    return _resolve_reference_uri_with_document(base_uri, reference)[0]


def resolve_reference(resolver: jsonschema_rs.Resolver, reference: str) -> tuple[jsonschema_rs.Resolver, Any]:
    return resolve_reference_with_uri(resolver, reference)[1:]


def _resolve_local_fragment(document_uri: str, fragment: str) -> Any:
    """Walk the cached root schema for `document_uri` to `fragment`.

    Returns `UNRESOLVABLE` if the document is not registered or the fragment cannot be
    walked. Per-document caching makes repeated lookups of the same fragment O(1).
    """
    original = _ROOT_SCHEMAS.get(document_uri)
    if original is None:
        return UNRESOLVABLE
    cache = _FRAGMENT_CACHES.get(document_uri)
    if cache is None:
        cache = _FRAGMENT_CACHES[document_uri] = {}
    cached = cache.get(fragment, _FRAGMENT_MISS)
    if cached is not _FRAGMENT_MISS:
        return cached
    # `#` and `#/` both reference the document root in common OpenAPI usage, even though
    # strictly RFC 6901 says `/` is `schema[""]`. Treating `/` as the root matches what
    # `RefResolver` did and what real-world schemas (e.g. multi-file YAML) rely on.
    if not fragment or fragment == "/":
        value: Any = original
    else:
        # Tolerate refs like `#components/parameters/X` (no leading `/`) that schemas in
        # the wild produce; legacy `RefResolver.resolve` accepted them.
        pointer = fragment if fragment.startswith("/") else f"/{fragment}"
        value = resolve_pointer(original, pointer)
    cache[fragment] = value
    return value


def resolve_reference_with_uri(
    resolver: jsonschema_rs.Resolver, reference: str
) -> tuple[str, jsonschema_rs.Resolver, Any]:
    """Resolve a `$ref` and return `(target_uri, target_resolver, target_value)`.

    The bundler needs the absolute target URI for cycle and visited-set bookkeeping
    in addition to the resolved value, so both are returned together.
    """
    try:
        resolved_uri, current_document_uri = _resolve_reference_uri_with_document(resolver.base_uri, reference)
        document_uri, _, fragment = resolved_uri.partition("#")

        if document_uri and document_uri != current_document_uri:
            document = retrieve(document_uri)
            external_resolver = make_root_resolver(document, location=document_uri)
            if fragment:
                return resolve_reference_with_uri(external_resolver, f"#{fragment}")
            return resolved_uri, external_resolver, document

        # Local fragment: walk the original schema to preserve key insertion order.
        # `jsonschema_rs.Resolver.lookup()` returns a deep-cloned dict with sorted keys,
        # which silently breaks order-sensitive callers (e.g. HTTP method iteration on
        # `$ref`-ed path items, or link order in stateful inference).
        value = _resolve_local_fragment(current_document_uri, fragment)
        if value is not UNRESOLVABLE:
            return resolved_uri, resolver, value

        resolved = resolver.lookup(reference)
    except (jsonschema_rs.ReferencingError, OSError, RemoteDocumentError, requests.RequestException) as exc:
        error = RefResolutionError(str(exc))
        if sys.version_info >= (3, 11):
            error.add_note(reference)
        else:
            error.__notes__ = [reference]
        raise error from exc
    return resolved_uri, resolved.resolver, resolved.contents
