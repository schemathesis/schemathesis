from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from schemathesis.core.errors import InfiniteRecursiveReference
from schemathesis.core.jsonschema.references import sanitize
from schemathesis.core.jsonschema.types import JsonSchema, to_json_type_name
from schemathesis.core.transforms import deepclone

if TYPE_CHECKING:
    from schemathesis.core.compat import RefResolver


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

    def bundle(self, schema: JsonSchema, resolver: RefResolver, *, inline_recursive: bool) -> Bundle:
        """Bundle a JSON Schema by embedding all references."""
        # Inlining recursive reference is required (for now) for data generation, but is unsound for data validation
        if not isinstance(schema, dict):
            return Bundle(schema=schema, name_to_uri={})

        # Track visited URIs and their local definition names
        inlining_for_recursion: set[str] = set()
        visited: set[str] = set()
        uri_to_name: dict[str, str] = {}
        defs = {}

        has_recursive_references = False
        resolve = resolver.resolve
        visit = visited.add

        def get_def_name(uri: str) -> str:
            """Generate or retrieve the local definition name for a URI."""
            name = uri_to_name.get(uri)
            if name is None:
                self.counter += 1
                name = f"schema{self.counter}"
                uri_to_name[uri] = name
            return name

        def bundle_recursive(current: JsonSchema | list[JsonSchema]) -> JsonSchema | list[JsonSchema]:
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
                        return {key: _bundle_recursive(value) for key, value in current.items() if key != "$ref"}
                    resolved_uri, resolved_schema = resolve(reference)

                    if not isinstance(resolved_schema, (dict, bool)):
                        raise BundleError(reference, resolved_schema)
                    def_name = get_def_name(resolved_uri)

                    scopes = resolver._scopes_stack

                    is_recursive_reference = resolved_uri in scopes
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
                            cycle = scopes[scopes.index(resolved_uri) :]
                            raise InfiniteRecursiveReference(reference, cycle)

                        # Track that we're inlining this schema
                        inlining_for_recursion.add(resolved_uri)
                        try:
                            cloned = deepclone(resolved_schema)
                            # Sanitize to remove optional recursive references
                            sanitize(cloned)

                            result = {key: _bundle_recursive(value) for key, value in current.items() if key != "$ref"}
                            bundled_clone = _bundle_recursive(cloned)
                            assert isinstance(bundled_clone, dict)
                            result.update(bundled_clone)
                            return result
                        finally:
                            inlining_for_recursion.discard(resolved_uri)
                    elif resolved_uri not in visited:
                        # Bundle only new schemas
                        visit(resolved_uri)

                        # Recursively bundle the embedded schema too!
                        resolver.push_scope(resolved_uri)
                        try:
                            bundled_resolved = _bundle_recursive(resolved_schema)
                        finally:
                            resolver.pop_scope()

                        defs[def_name] = bundled_resolved

                        return {
                            key: f"{REFERENCE_TO_BUNDLE_PREFIX}/{def_name}"
                            if key == "$ref"
                            else _bundle_recursive(value)
                            if isinstance(value, (dict, list))
                            else value
                            for key, value in current.items()
                        }
                    else:
                        # Already visited - just update $ref
                        return {
                            key: f"{REFERENCE_TO_BUNDLE_PREFIX}/{def_name}"
                            if key == "$ref"
                            else _bundle_recursive(value)
                            if isinstance(value, (dict, list))
                            else value
                            for key, value in current.items()
                        }
                return {
                    key: _bundle_recursive(value) if isinstance(value, (dict, list)) else value
                    for key, value in current.items()
                }
            elif isinstance(current, list):
                return [_bundle_recursive(item) if isinstance(item, (dict, list)) else item for item in current]  # type: ignore[misc]
            # `isinstance` guards won't let it happen
            # Otherwise is present to make type checker happy
            return current  # pragma: no cover

        bundled = bundle_recursive(schema)

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


def bundle(schema: JsonSchema, resolver: RefResolver, *, inline_recursive: bool) -> Bundle:
    """Bundle a JSON Schema by embedding all references."""
    return Bundler().bundle(schema, resolver, inline_recursive=inline_recursive)


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
                            schema_name = original_uri.split("#/components/schemas/")[1]
                            components["schemas"][schema_name] = unbundle(bundled_schema, name_to_uri)
                        elif "#/definitions/" in original_uri:
                            schema_name = original_uri.split("#/definitions/")[1]
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
