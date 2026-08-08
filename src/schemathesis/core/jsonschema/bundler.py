from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from schemathesis.core.jsonschema.resolver import (
    Resolver,
    resolve_reference_uri,
    resolve_reference_with_uri,
)
from schemathesis.core.jsonschema.types import JsonSchema, to_json_type_name
from schemathesis.core.transforms import decode_pointer

BUNDLE_STORAGE_KEY = "x-bundled"
REFERENCE_TO_BUNDLE_PREFIX = f"#/{BUNDLE_STORAGE_KEY}"
# Cache for bundled parameters: parameter object id -> (parameter, bundled definition, name_to_uri mapping).
# The parameter is stored so that its address stays reserved - otherwise a freed parameter's address can be
# handed to an unrelated one, which would then hit the wrong entry.
BundleCache = dict[int, tuple[Mapping[str, Any], dict[str, Any], dict[str, str]]]


IDENTITY_KEYWORDS = ("$id", "id")


def _without_identity(schema: JsonSchema | list[JsonSchema]) -> JsonSchema | list[JsonSchema]:
    """The schema without the name it gave itself.

    A bundled definition is reachable only as `#/x-bundled/<name>`, and a name it kept would set a
    base URI that every later pointer resolves against - a bare fragment breaks them outright.
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
    """Bundler tracks schema ids stored in a bundle."""

    counter: int

    __slots__ = ("counter",)

    def __init__(self) -> None:
        self.counter = 0

    def bundle(self, schema: JsonSchema, resolver: Resolver) -> Bundle:
        """Bundle a JSON Schema by embedding all references."""
        if not isinstance(schema, dict):
            return Bundle(schema=schema, name_to_uri={})

        # Track visited URIs and their local definition names
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

        def bundle_recursive(
            current: JsonSchema | list[JsonSchema],
            current_resolver: Resolver,
        ) -> JsonSchema | list[JsonSchema]:
            """Recursively process and bundle references in the current schema."""
            # Local lookup is cheaper and it matters for large schemas.
            # It works because this recursive call goes to every nested value
            nonlocal has_recursive_references
            _bundle_recursive = bundle_recursive
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
                        # Bundle only new schemas
                        visit(resolved_uri)

                        # Recursively bundle the embedded schema too!
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
                        # Already visited - just update $ref
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
            # `isinstance` guards won't let it happen
            # Otherwise is present to make type checker happy
            return current  # pragma: no cover

        bundled = bundle_recursive(schema, resolver)

        assert isinstance(bundled, dict)

        # A single target that never points back at itself reads the same spelled out in place.
        if not has_recursive_references and "$ref" in bundled and len(defs) == 1:
            result = {key: value for key, value in bundled.items() if key != "$ref"}
            for value in defs.values():
                if isinstance(value, dict):
                    result.update(value)
            return Bundle(schema=result, name_to_uri={})

        if defs:
            bundled[BUNDLE_STORAGE_KEY] = defs
        return Bundle(schema=bundled, name_to_uri={v: k for k, v in uri_to_name.items()})


def bundle(schema: JsonSchema, resolver: Resolver) -> Bundle:
    """Gather every reachable reference target into the schema, keeping the references themselves."""
    return Bundler().bundle(schema, resolver)


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
            elif isinstance(value, (dict, list)):
                result[key] = unbundle(value, name_to_uri)
            else:
                result[key] = value
        return result
    elif isinstance(schema, list):
        return [unbundle(item, name_to_uri) for item in schema]  # type: ignore[return-value]
    return schema
