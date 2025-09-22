from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemathesis.core.compat import RefResolver
from schemathesis.core.errors import InfiniteRecursiveReference
from schemathesis.core.jsonschema.references import sanitize
from schemathesis.core.jsonschema.types import JsonSchema, to_json_type_name
from schemathesis.core.transforms import deepclone

BUNDLE_STORAGE_KEY = "x-bundled"
REFERENCE_TO_BUNDLE_PREFIX = f"#/{BUNDLE_STORAGE_KEY}"


class BundleError(Exception):
    def __init__(self, reference: str, value: Any) -> None:
        self.reference = reference
        self.value = value

    def __str__(self) -> str:
        return f"Cannot bundle `{self.reference}`: expected JSON Schema (object or boolean), got {to_json_type_name(self.value)}"


@dataclass
class Bundler:
    """Bundler tracks schema ids stored in a bundle."""

    counter: int

    __slots__ = ("counter",)

    def __init__(self) -> None:
        self.counter = 0

    def bundle(self, schema: JsonSchema, resolver: RefResolver) -> JsonSchema:
        """Bundle a JSON Schema by embedding all references."""
        if isinstance(schema, bool):
            return schema

        bundled = deepclone(schema)

        # Track visited URIs and their local definition names
        visited: set[str] = set()
        uri_to_def_name: dict[str, str] = {}
        defs = {}

        resolve = resolver.resolve
        visit = visited.add

        def get_def_name(uri: str) -> str:
            """Generate or retrieve the local definition name for a URI."""
            if uri not in uri_to_def_name:
                self.counter += 1
                uri_to_def_name[uri] = f"schema{self.counter}"
            return uri_to_def_name[uri]

        def bundle_recursive(current: JsonSchema | list[JsonSchema]) -> None:
            """Recursively process and bundle references in the current schema."""
            if isinstance(current, dict):
                reference = current.get("$ref")
                if isinstance(reference, str) and not reference.startswith(REFERENCE_TO_BUNDLE_PREFIX):
                    resolved_uri, resolved_schema = resolve(reference)

                    if not isinstance(resolved_schema, (dict, bool)):
                        raise BundleError(reference, resolved_schema)
                    def_name = get_def_name(resolved_uri)

                    if resolved_uri in resolver._scopes_stack:
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
                        cloned = deepclone(resolved_schema)
                        remaining_references = sanitize(cloned)
                        if remaining_references:
                            # This schema is either infinitely recursive or the sanitization logic misses it, in any
                            # event, we git up here
                            raise InfiniteRecursiveReference(reference)
                        del current["$ref"]
                        current.update(cloned)
                    elif resolved_uri not in visited:
                        # Bundle only new schemas
                        visit(resolved_uri)

                        cloned = deepclone(resolved_schema)
                        defs[def_name] = cloned

                        # Recursively bundle the embedded schema too!
                        resolver.push_scope(resolved_uri)
                        try:
                            bundle_recursive(cloned)
                        finally:
                            resolver.pop_scope()
                        current["$ref"] = f"{REFERENCE_TO_BUNDLE_PREFIX}/{def_name}"
                    else:
                        current["$ref"] = f"{REFERENCE_TO_BUNDLE_PREFIX}/{def_name}"

                for value in current.values():
                    bundle_recursive(value)

            elif isinstance(current, list):
                for item in current:
                    bundle_recursive(item)

        bundle_recursive(bundled)

        if defs:
            bundled[BUNDLE_STORAGE_KEY] = defs
        return bundled


def bundle(schema: JsonSchema, resolver: RefResolver) -> JsonSchema:
    return Bundler().bundle(schema, resolver)
