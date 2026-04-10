from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jsonschema_rs

from schemathesis.core.errors import InfiniteRecursiveReference
from schemathesis.core.jsonschema.references import sanitize
from schemathesis.core.jsonschema.resolver import (
    resolve_reference_uri,
    resolve_reference_with_uri,
)
from schemathesis.core.jsonschema.types import JsonSchema, to_json_type_name
from schemathesis.core.transforms import decode_pointer, deepclone

BUNDLE_STORAGE_KEY = "x-bundled"
REFERENCE_TO_BUNDLE_PREFIX = f"#/{BUNDLE_STORAGE_KEY}"

# Cache for bundled parameters: parameter object id -> (bundled definition, name_to_uri mapping)
BundleCache = dict[int, tuple[dict[str, Any], dict[str, str]]]


class BundleError(Exception):
    def __init__(self, reference: str, value: Any) -> None:
        self.reference = reference
        self.value = value

    def __str__(self) -> str:
        return f"Cannot bundle `{self.reference}`: expected JSON Schema (object or boolean), got {to_json_type_name(self.value)}"


@dataclass
class Bundle:
    schema: JsonSchema
    name_to_uri: dict[str, str]

    __slots__ = ("schema", "name_to_uri")


class Bundler:
    """Bundler tracks schema ids stored in a bundle."""

    counter: int

    __slots__ = ("counter",)

    def __init__(self) -> None:
        self.counter = 0

    def bundle(self, schema: JsonSchema, resolver: jsonschema_rs.Resolver, *, inline_recursive: bool) -> Bundle:
        """Bundle a JSON Schema by embedding all references."""
        # Inlining recursive reference is required (for now) for data generation, but is unsound for data validation
        if not isinstance(schema, dict):
            return Bundle(schema=schema, name_to_uri={})

        # Track visited URIs and their local definition names
        inlining_for_recursion: set[str] = set()
        visited: set[str] = set()
        uri_to_name: dict[str, str] = {}
        defs = {}
        scope_stack: list[str] = []

        has_recursive_references = False
        visit = visited.add

        def get_def_name(uri: str) -> str:
            """Generate or retrieve the local definition name for a URI."""
            name = uri_to_name.get(uri)
            if name is None:
                self.counter += 1
                name = f"schema{self.counter}"
                uri_to_name[uri] = name
            return name

        def bundle_value(
            key: str,
            value: Any,
            current_resolver: jsonschema_rs.Resolver,
        ) -> Any:
            """Walk a child value, with per-variant exception handling for `oneOf`/`anyOf`."""
            if isinstance(value, list) and key in ("oneOf", "anyOf"):
                survivors: list = []
                last_error: InfiniteRecursiveReference | None = None
                for item in value:
                    if not isinstance(item, dict | list):
                        survivors.append(item)
                        continue
                    try:
                        survivors.append(bundle_recursive(item, current_resolver))
                    except InfiniteRecursiveReference as exc:
                        # The variant cycles back; drop it. Kept alternatives still
                        # satisfy the original combinator. If none survive, re-raise —
                        # the cycle is unbreakable.
                        last_error = exc
                if not survivors and last_error is not None:
                    raise last_error
                return survivors
            if isinstance(value, dict | list):
                return bundle_recursive(value, current_resolver)
            return value

        def bundle_recursive(
            current: JsonSchema | list[JsonSchema],
            current_resolver: jsonschema_rs.Resolver,
        ) -> JsonSchema | list[JsonSchema]:
            """Recursively process and bundle references in the current schema."""
            # Local lookup is cheaper and it matters for large schemas.
            # It works because this recursive call goes to every nested value
            nonlocal has_recursive_references
            _bundle_recursive = bundle_recursive
            _bundle_value = bundle_value
            if isinstance(current, dict):
                reference = current.get("$ref")
                if isinstance(reference, str) and not reference.startswith(REFERENCE_TO_BUNDLE_PREFIX):
                    # Empty references resolve to the current scope and are not useful for test generation
                    if not reference.strip():
                        return {
                            key: _bundle_recursive(value, current_resolver)
                            for key, value in current.items()
                            if key != "$ref"
                        }
                    # Fast path for duplicate refs: skip the schema retrieval if we've
                    # already bundled this URI (and we're not currently inlining it).
                    if visited:
                        candidate_uri = resolve_reference_uri(current_resolver.base_uri, reference)
                        if candidate_uri in visited and candidate_uri not in scope_stack:
                            def_name = get_def_name(candidate_uri)
                            return {
                                key: f"{REFERENCE_TO_BUNDLE_PREFIX}/{def_name}"
                                if key == "$ref"
                                else _bundle_value(key, value, current_resolver)
                                for key, value in current.items()
                            }
                    resolved_uri, next_resolver, resolved_schema = resolve_reference_with_uri(
                        current_resolver, reference
                    )

                    if not isinstance(resolved_schema, dict | bool):
                        raise BundleError(reference, resolved_schema)
                    def_name = get_def_name(resolved_uri)

                    is_recursive_reference = resolved_uri in scope_stack
                    has_recursive_references |= is_recursive_reference
                    if inline_recursive and is_recursive_reference:
                        # This is a recursive reference! As of Sep 2025, `hypothesis-jsonschema` does not support
                        # recursive references and Schemathesis has to remove them if possible.
                        #
                        # Cutting them of immediately would limit the quality of generated data, since it would have
                        # just a single level of recursion. Currently, the only way to generate recursive data is to
                        # inline definitions directly, which can lead to schema size explosion.
                        #
                        # To balance it, Schemathesis inlines one level, that avoids exponential blowup of O(B ^ L)
                        # in worst case, where B is branching factor (number of recursive references per schema), and
                        # L is the number of levels. Even quadratic growth can be unacceptable for large schemas.
                        #
                        # In the future, it **should** be handled by `hypothesis-jsonschema` instead.
                        if resolved_uri in inlining_for_recursion:
                            # Check if we're already trying to inline this schema
                            # If yes, it means we have an unbreakable cycle
                            cycle = scope_stack[scope_stack.index(resolved_uri) :]
                            raise InfiniteRecursiveReference(reference, cycle)

                        # Track that we're inlining this schema
                        inlining_for_recursion.add(resolved_uri)
                        try:
                            cloned = deepclone(resolved_schema)

                            def _is_recursive(ref: str) -> bool:
                                try:
                                    target_uri = resolve_reference_uri(next_resolver.base_uri, ref)
                                except Exception:
                                    return False
                                return target_uri in inlining_for_recursion

                            # Sanitize to remove optional recursive references
                            sanitize(cloned, is_recursive_ref=_is_recursive)

                            result = {
                                key: _bundle_value(key, value, current_resolver)
                                for key, value in current.items()
                                if key != "$ref"
                            }
                            scope_stack.append(resolved_uri)
                            try:
                                bundled_clone = _bundle_recursive(cloned, next_resolver)
                            finally:
                                scope_stack.pop()
                            assert isinstance(bundled_clone, dict)
                            result.update(bundled_clone)
                            return result
                        finally:
                            inlining_for_recursion.discard(resolved_uri)
                    elif resolved_uri not in visited:
                        # Bundle only new schemas
                        visit(resolved_uri)

                        # Recursively bundle the embedded schema too!
                        scope_stack.append(resolved_uri)
                        try:
                            bundled_resolved = _bundle_recursive(resolved_schema, next_resolver)
                        except InfiniteRecursiveReference:
                            # Undo `visit` so a sibling path can retry resolving this URI;
                            # otherwise we'd emit `$ref: #/x-bundled/{def_name}` with no
                            # entry in `defs`, leaving a dangling pointer in the bundle.
                            visited.discard(resolved_uri)
                            raise
                        finally:
                            scope_stack.pop()

                        defs[def_name] = bundled_resolved

                        return {
                            key: f"{REFERENCE_TO_BUNDLE_PREFIX}/{def_name}"
                            if key == "$ref"
                            else _bundle_value(key, value, current_resolver)
                            for key, value in current.items()
                        }
                    else:
                        # Already visited - just update $ref
                        return {
                            key: f"{REFERENCE_TO_BUNDLE_PREFIX}/{def_name}"
                            if key == "$ref"
                            else _bundle_value(key, value, current_resolver)
                            for key, value in current.items()
                        }
                return {key: _bundle_value(key, value, current_resolver) for key, value in current.items()}
            elif isinstance(current, list):
                result_list: list[JsonSchema] = [
                    _bundle_recursive(item, current_resolver)  # type: ignore[misc]
                    if isinstance(item, dict | list)
                    else item
                    for item in current
                ]
                return result_list
            # `isinstance` guards won't let it happen
            # Otherwise is present to make type checker happy
            return current  # pragma: no cover

        bundled = bundle_recursive(schema, resolver)

        assert isinstance(bundled, dict)

        # Inlining such a schema is only possible if recursive references were inlined
        if (inline_recursive or not has_recursive_references) and "$ref" in bundled and len(defs) == 1:
            result = {key: value for key, value in bundled.items() if key != "$ref"}
            for value in defs.values():
                if isinstance(value, dict):
                    result.update(value)
            return Bundle(schema=result, name_to_uri={})

        if defs:
            bundled[BUNDLE_STORAGE_KEY] = defs
        return Bundle(schema=bundled, name_to_uri={v: k for k, v in uri_to_name.items()})

    def prepare_for_generation(self, schema: JsonSchema, resolver: jsonschema_rs.Resolver) -> Bundle:
        """Prepare schema for data generation by inlining recursive references."""
        return self.bundle(schema, resolver, inline_recursive=True)

    def prepare_for_validation(self, schema: JsonSchema, resolver: jsonschema_rs.Resolver) -> Bundle:
        """Prepare schema for validation while preserving recursive references."""
        return self.bundle(schema, resolver, inline_recursive=False)


def bundle(schema: JsonSchema, resolver: jsonschema_rs.Resolver, *, inline_recursive: bool) -> Bundle:
    """Bundle a JSON Schema by embedding all references."""
    return Bundler().bundle(schema, resolver, inline_recursive=inline_recursive)


def prepare_for_generation(schema: JsonSchema, resolver: jsonschema_rs.Resolver) -> Bundle:
    """Prepare schema for data generation by inlining recursive references."""
    return Bundler().prepare_for_generation(schema, resolver)


def prepare_for_validation(schema: JsonSchema, resolver: jsonschema_rs.Resolver) -> Bundle:
    """Prepare schema for validation while preserving recursive references."""
    return Bundler().prepare_for_validation(schema, resolver)


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
            if "#" in uri:
                fragment = uri.split("#", 1)[1]
                if fragment.startswith("/"):
                    result.extend(decode_pointer(segment) for segment in fragment[1:].split("/"))
            i += 2
        else:
            result.append(path[i])
            i += 1
    return result


def unbundle(schema: JsonSchema | list[JsonSchema], name_to_uri: dict[str, str]) -> JsonSchema:
    """Restore original $ref paths in a bundled schema for display purposes."""
    if isinstance(schema, dict):
        result: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "$ref" and isinstance(value, str) and value.startswith(REFERENCE_TO_BUNDLE_PREFIX):
                bundled_name = value.split("/")[-1]
                if bundled_name in name_to_uri:
                    original_uri = name_to_uri[bundled_name]
                    if "#" in original_uri:
                        result[key] = "#" + original_uri.split("#", 1)[1]
                    else:
                        result[key] = value
                else:
                    result[key] = value
            elif key == BUNDLE_STORAGE_KEY and isinstance(value, dict):
                components: dict[str, dict[str, Any]] = {"schemas": {}}
                for bundled_name, bundled_schema in value.items():
                    if bundled_name in name_to_uri:
                        original_uri = name_to_uri[bundled_name]
                        if "#/components/schemas/" in original_uri:
                            schema_name = decode_pointer(original_uri.split("#/components/schemas/")[1])
                            components["schemas"][schema_name] = unbundle(bundled_schema, name_to_uri)
                        elif "#/definitions/" in original_uri:
                            schema_name = decode_pointer(original_uri.split("#/definitions/")[1])
                            components["schemas"][schema_name] = unbundle(bundled_schema, name_to_uri)
                        else:
                            components["schemas"][bundled_name] = unbundle(bundled_schema, name_to_uri)
                    else:
                        components["schemas"][bundled_name] = unbundle(bundled_schema, name_to_uri)
                result["components"] = components
            elif isinstance(value, dict | list):
                result[key] = unbundle(value, name_to_uri)
            else:
                result[key] = value
        return result
    elif isinstance(schema, list):
        return [unbundle(item, name_to_uri) for item in schema]  # type: ignore[return-value]
    return schema
