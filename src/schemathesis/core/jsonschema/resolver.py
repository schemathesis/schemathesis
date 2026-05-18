from __future__ import annotations

import os
import sys
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote, urldefrag, urljoin, urlsplit, urlunsplit
from urllib.request import urlopen

import jsonschema_rs
import requests

from schemathesis.core.compat import RefResolutionError
from schemathesis.core.deserialization import deserialize_yaml
from schemathesis.core.errors import RemoteDocumentError
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.core.transforms import UNRESOLVABLE, resolve_pointer
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT

IN_MEMORY_BASE_URI = "urn:schemathesis:root"
_FRAGMENT_MISS = object()


class Resolver:
    """Wraps `jsonschema_rs.Resolver` with a bound schema and per-instance fragment cache.

    The bound schema lets us walk local fragments directly instead of going through
    `Registry.lookup()`, which deep-clones the result every call. The cache is per
    instance so multiple in-memory resolvers don't share or invalidate each other's
    walk results.
    """

    __slots__ = ("base_uri", "inner", "schema", "fragment_cache")

    def __init__(self, inner: jsonschema_rs.Resolver, schema: JsonSchema) -> None:
        self.inner = inner
        self.schema = schema
        # Frozen on the wrapper so hot-path callers don't pay a property+attribute hop.
        self.base_uri: str = inner.base_uri
        self.fragment_cache: dict[str, Any] = {}

    def lookup(self, reference: str) -> Any:
        return self.inner.lookup(reference)

    def resolve_local_fragment(self, fragment: str) -> Any:
        """Walk the bound schema to `fragment`. Returns `UNRESOLVABLE` if the walk fails."""
        cache = self.fragment_cache
        cached = cache.get(fragment, _FRAGMENT_MISS)
        if cached is not _FRAGMENT_MISS:
            return cached
        # `#` and `#/` both reference the document root in common OpenAPI usage, even though
        # strictly RFC 6901 says `/` is `schema[""]`. Real-world multi-file YAML schemas
        # rely on the lenient interpretation.
        if not fragment or fragment == "/":
            value: Any = self.schema
        else:
            # Tolerate refs like `#components/parameters/X` (no leading `/`) that schemas
            # in the wild produce.
            pointer = fragment if fragment.startswith("/") else f"/{fragment}"
            value = resolve_pointer(self.schema, pointer)
        cache[fragment] = value
        return value


# RFC 3986 §3.3 path characters plus `%` so already percent-encoded sequences pass through.
_SAFE_URI_PATH_CHARS = "/:@!$&'()*+,;=%-._~"


def _quote_unsafe_uri_path(uri: str) -> str:
    """Percent-encode reserved characters in a URI's path component.

    `urljoin` does not percent-encode, so a relative `$ref` like
    `./paths/{id}/op.yaml` produces a URI with raw `{`/`}` that
    `jsonschema_rs.Registry` rejects as an invalid URI reference.
    """
    parts = urlsplit(uri)
    quoted_path = quote(parts.path, safe=_SAFE_URI_PATH_CHARS)
    if quoted_path == parts.path:
        return uri
    return urlunsplit(parts._replace(path=quoted_path))


def _normalize_location(location: str) -> str:
    """Convert a plain filesystem path to a `file://` URI so relative refs resolve via urljoin.

    Without this, `jsonschema_rs.Registry` rewrites plain paths to `json-schema:///...`,
    a scheme that `urljoin` does not treat as relative-aware.
    """
    if "://" in location or location.startswith("urn:"):
        return _quote_unsafe_uri_path(location)
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
        # Schema content can fail registry construction in ways we want to defer:
        # YAML can parse bare `on:`/`off:` as bool keys, and a single broken external
        # `$ref` (e.g. `./other.json#/...` from an in-memory schema) trips eager ref
        # resolution. Registering an empty schema lets loading succeed; local fragment
        # lookups go through the resolver's bound schema, and unresolvable refs surface
        # per-operation.
        return jsonschema_rs.Registry([(base_uri, {})], draft=draft, retriever=retrieve)


def make_root_resolver(
    root_schema: dict[str, Any],
    *,
    location: str | None = None,
    draft: int | None = None,
) -> Resolver:
    registry = build_registry(root_schema, location=location, draft=draft)
    base_uri = _normalize_location(location) if location else IN_MEMORY_BASE_URI
    return Resolver(registry.resolver(base_uri), root_schema)


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


def resolve_reference(resolver: Resolver, reference: str) -> tuple[Resolver, Any]:
    return resolve_reference_with_uri(resolver, reference)[1:]


def resolve_reference_with_uri(resolver: Resolver, reference: str) -> tuple[str, Resolver, Any]:
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

        # Local fragment: walk the bound schema directly. `Registry.lookup()` deep-clones
        # the result on every call; walking the original returns a reference and is faster.
        value = resolver.resolve_local_fragment(fragment)
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
    # Bind `resolved.contents` so subsequent local-fragment walks land in the right document.
    return resolved_uri, Resolver(resolved.resolver, resolved.contents), resolved.contents
