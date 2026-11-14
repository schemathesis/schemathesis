from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from schemathesis.core.cache import MISSING, BoundedCache
from schemathesis.core.jsonschema.resolver import (
    Resolver,
    resolve_reference_uri,
    resolve_reference_with_uri,
)
from schemathesis.core.jsonschema.types import JsonSchema, to_json_type_name
from schemathesis.core.transforms import decode_pointer, encode_pointer

BUNDLE_STORAGE_KEY = "x-bundled"
REFERENCE_TO_BUNDLE_PREFIX = f"#/{BUNDLE_STORAGE_KEY}"
# Parameter id -> (parameter, bundled definition, name_to_uri); holding the parameter keeps its id from being reused.
BundleCache = dict[int, tuple[Mapping[str, Any], dict[str, Any], dict[str, str]]]


IDENTITY_KEYWORDS = ("$id", "id")


def _without_identity(schema: JsonSchema | list[JsonSchema]) -> JsonSchema | list[JsonSchema]:
    """The schema without its `$id` / `id`.

    A bundled definition lives at `#/x-bundled/<name>`; a kept `$id` would rebase every pointer inside it.
    """
    if not isinstance(schema, dict) or ("$id" not in schema and "id" not in schema):
        return schema
    # A non-string sits under a property named `$id`/`id`, not under the keyword.
    return {key: value for key, value in schema.items() if key not in IDENTITY_KEYWORDS or not isinstance(value, str)}


class BundleError(Exception):
    def __init__(self, reference: str, value: object) -> None:
        self.reference = reference
        self.value = value

    def __str__(self) -> str:
        return f"Cannot bundle `{self.reference}`: expected JSON Schema (object or boolean), got {to_json_type_name(self.value)}"


@dataclass(slots=True)
class Bundle:
    schema: JsonSchema
    name_to_uri: dict[str, str]


class Bundler:
    counter: int
    # Whether keywords next to `$ref` apply (Draft 2019-09+) or are ignored (Draft 4).
    ref_siblings: bool

    __slots__ = ("counter", "ref_siblings")

    def __init__(self, *, ref_siblings: bool = True) -> None:
        self.counter = 0
        self.ref_siblings = ref_siblings

    def bundle(self, schema: JsonSchema, resolver: Resolver) -> Bundle:
        if not isinstance(schema, dict):
            return Bundle(schema=schema, name_to_uri={})

        visited: set[str] = set()
        uri_to_name: dict[str, str] = {}
        defs = {}
        scope_stack: list[str] = []

        has_recursive_references = False
        reference_count = 0
        visit = visited.add

        def get_def_name(uri: str) -> str:
            nonlocal reference_count
            reference_count += 1
            name = uri_to_name.get(uri)
            if name is None:
                self.counter += 1
                # Fixed width, so names sort in creation order however many schemas were bundled before.
                name = f"schema{self.counter:06d}"
                uri_to_name[uri] = name
            return name

        def bundle_recursive(
            current: JsonSchema | list[JsonSchema],
            current_resolver: Resolver,
            identity_handled: bool = False,
        ) -> JsonSchema | list[JsonSchema]:
            # A local alias is cheaper than a closure lookup on every nested value of large schemas.
            nonlocal has_recursive_references
            _bundle_recursive = bundle_recursive
            if isinstance(current, dict):
                if ("$id" in current or "id" in current) and not identity_handled:
                    current_resolver = current_resolver.enter(current)
                    if current is not schema:
                        references_before = reference_count
                        bundled_named = _bundle_recursive(current, current_resolver, True)
                        # References into the bundle resolve against the closest `$id` above them, not the root holding it.
                        if reference_count != references_before:
                            return _without_identity(bundled_named)
                        return bundled_named
                reference = current.get("$ref")
                if isinstance(reference, str) and not reference.startswith(REFERENCE_TO_BUNDLE_PREFIX):
                    # Empty references resolve to the current scope and are not useful for test generation
                    if not reference.strip():
                        return {
                            key: _bundle_recursive(value, current_resolver)
                            for key, value in current.items()
                            if key != "$ref"
                        }
                    # Skip retrieval for a URI already bundled and not currently being inlined.
                    if visited:
                        candidate_uri = resolve_reference_uri(current_resolver.base_uri, reference)
                        if candidate_uri in visited and candidate_uri not in scope_stack:
                            def_name = get_def_name(candidate_uri)
                            return {
                                key: f"{REFERENCE_TO_BUNDLE_PREFIX}/{def_name}"
                                if key == "$ref"
                                else (
                                    _bundle_recursive(value, current_resolver)
                                    if isinstance(value, (dict, list))
                                    else value
                                )
                                for key, value in current.items()
                            }
                    resolved_uri, next_resolver, resolved_schema = resolve_reference_with_uri(
                        current_resolver, reference
                    )

                    if not isinstance(resolved_schema, (dict, bool)):
                        raise BundleError(reference, resolved_schema)
                    def_name = get_def_name(resolved_uri)

                    is_recursive_reference = resolved_uri in scope_stack
                    has_recursive_references |= is_recursive_reference
                    if resolved_uri not in visited:
                        visit(resolved_uri)

                        scope_stack.append(resolved_uri)
                        try:
                            bundled_resolved = _bundle_recursive(resolved_schema, next_resolver)
                        finally:
                            scope_stack.pop()

                        defs[def_name] = _without_identity(bundled_resolved)

                        return {
                            key: f"{REFERENCE_TO_BUNDLE_PREFIX}/{def_name}"
                            if key == "$ref"
                            else (
                                _bundle_recursive(value, current_resolver) if isinstance(value, (dict, list)) else value
                            )
                            for key, value in current.items()
                        }
                    else:
                        return {
                            key: f"{REFERENCE_TO_BUNDLE_PREFIX}/{def_name}"
                            if key == "$ref"
                            else (
                                _bundle_recursive(value, current_resolver) if isinstance(value, (dict, list)) else value
                            )
                            for key, value in current.items()
                        }
                return {
                    key: _bundle_recursive(value, current_resolver) if isinstance(value, (dict, list)) else value
                    for key, value in current.items()
                }
            elif isinstance(current, list):
                result_list: list[JsonSchema] = [
                    _bundle_recursive(item, current_resolver)  # type: ignore[misc]
                    if isinstance(item, (dict, list))
                    else item
                    for item in current
                ]
                return result_list
            # Unreachable, satisfies the type checker
            return current  # pragma: no cover

        bundled = bundle_recursive(schema, resolver)

        assert isinstance(bundled, dict)

        # Inline a root reference's lone non-recursive target unless a sibling also references it or shares a keyword.
        # Where siblings are ignored, inlining the target next to them would enforce them.
        if (
            not has_recursive_references
            and "$ref" in bundled
            and len(defs) == 1
            and reference_count == 1
            and (self.ref_siblings or len(bundled) == 1)
        ):
            (target,) = defs.values()
            if not isinstance(target, dict) or bundled.keys().isdisjoint(target):
                result = {key: value for key, value in bundled.items() if key != "$ref"}
                if isinstance(target, dict):
                    result.update(target)
                return Bundle(schema=result, name_to_uri={})

        if defs:
            bundled[BUNDLE_STORAGE_KEY] = defs
        return Bundle(schema=bundled, name_to_uri={v: k for k, v in uri_to_name.items()})


def bundle(schema: JsonSchema, resolver: Resolver, *, ref_siblings: bool = True) -> Bundle:
    """Gather every reachable reference target into the schema, keeping the references themselves."""
    return Bundler(ref_siblings=ref_siblings).bundle(schema, resolver)


def unbundle_path(path: list[str | int], name_to_uri: dict[str, str]) -> list[str | int]:
    """Translate bundled path segments back to original reference path segments.

    E.g. ['x-bundled', 'schema1', 'properties', 'host'] with name_to_uri={'schema1': '#/components/schemas/Host'}
    becomes ['components', 'schemas', 'Host', 'properties', 'host'].
    """
    result: list[str | int] = []
    i = 0
    while i < len(path):
        next_key = path[i + 1] if i + 1 < len(path) else None
        if path[i] == BUNDLE_STORAGE_KEY and isinstance(next_key, str) and next_key in name_to_uri:
            uri = name_to_uri[next_key]
            fragment = uri.split("#", 1)[1] if "#" in uri else ""
            if fragment.startswith("/"):
                result.extend(decode_pointer(segment) for segment in fragment[1:].split("/"))
            else:
                # A whole-file reference has no pointer segments; name the referenced file instead.
                result.append(_document_display_name(uri))
            i += 2
        else:
            result.append(path[i])
            i += 1
    return result


def definition_location(name: str, name_to_uri: dict[str, str]) -> str:
    """Location of a bundled definition in the original document, e.g. `/components/schemas/Host`."""
    return "/" + "/".join(str(segment) for segment in unbundle_path([BUNDLE_STORAGE_KEY, name], name_to_uri))


def _document_display_name(uri: str) -> str:
    """Name of the referenced document inside a URI, e.g. `pet.json` in `file:///dir/pet.json`."""
    path = urlsplit(uri).path.rstrip("/").rsplit("/", 1)[-1]
    return path or uri


_UNBUNDLED_COMPONENTS_CACHE: BoundedCache = BoundedCache(maxsize=256)


def _unbundled_schema_name(bundled_name: str, original_uri: str | None) -> str:
    if original_uri is not None and "#/components/schemas/" in original_uri:
        return decode_pointer(original_uri.split("#/components/schemas/")[1])
    if original_uri is not None and "#/definitions/" in original_uri:
        return decode_pointer(original_uri.split("#/definitions/")[1])
    return bundled_name


def _unbundled_components(bundled: dict[str, Any], name_to_uri: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Every definition in the document under its original name, for display."""
    # Every reported failure unbundles the same whole-document storage; callers only read, so they share one result.
    cache_key = (id(bundled), id(name_to_uri))
    cached = _UNBUNDLED_COMPONENTS_CACHE.get(cache_key)
    if cached is not MISSING:
        return cached[0]
    schemas: dict[str, Any] = {}
    for bundled_name, bundled_schema in bundled.items():
        name = _unbundled_schema_name(bundled_name, name_to_uri.get(bundled_name))
        schemas[name] = unbundle(bundled_schema, name_to_uri)
    components = {"schemas": schemas}
    # The trailing elements pin the keyed objects, so their `id`s cannot be recycled into a stale hit.
    _UNBUNDLED_COMPONENTS_CACHE[cache_key] = (components, bundled, name_to_uri)
    return components


def unbundle_reference(reference: str, name_to_uri: dict[str, str]) -> str:
    """Restore the original form of a reference into the bundle, for display purposes."""
    bundled_name = reference.split("/")[-1]
    if bundled_name not in name_to_uri:
        return reference
    name = _unbundled_schema_name(bundled_name, name_to_uri[bundled_name])
    return f"#/components/schemas/{encode_pointer(name)}"


def unbundle(schema: JsonSchema | list[JsonSchema], name_to_uri: dict[str, str]) -> JsonSchema:
    """Restore original $ref paths in a bundled schema for display purposes."""
    if isinstance(schema, dict):
        result: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "$ref" and isinstance(value, str) and value.startswith(REFERENCE_TO_BUNDLE_PREFIX):
                result[key] = unbundle_reference(value, name_to_uri)
            elif key == BUNDLE_STORAGE_KEY and isinstance(value, dict):
                result["components"] = _unbundled_components(value, name_to_uri)
            elif isinstance(value, (dict, list)):
                result[key] = unbundle(value, name_to_uri)
            else:
                result[key] = value
        return result
    elif isinstance(schema, list):
        return [unbundle(item, name_to_uri) for item in schema]  # type: ignore[return-value]
    return schema
