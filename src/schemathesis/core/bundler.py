from __future__ import annotations

from typing import Any, Union

from schemathesis.core.compat import RefResolver
from schemathesis.core.transforms import deepclone

JsonSchema = Union[dict[str, Any], bool]


BUNDLE_STORAGE_KEY = "x-bundled"
REFERENCE_TO_BUNDLE_PREFIX = f"#/{BUNDLE_STORAGE_KEY}"


def bundle(schema: JsonSchema, resolver: RefResolver) -> JsonSchema:
    """Bundle a JSON Schema by embedding all references."""
    if isinstance(schema, bool):
        return schema

    bundled = deepclone(schema)

    # Track visited URIs and their local definition names
    visited: set[str] = set()
    uri_to_def_name: dict[str, str] = {}
    defs = {}
    counter = 0

    def get_def_name(uri: str) -> str:
        """Generate or retrieve the local definition name for a URI."""
        nonlocal counter
        if uri not in uri_to_def_name:
            counter += 1
            uri_to_def_name[uri] = f"schema{counter}"
        return uri_to_def_name[uri]

    def bundle_recursive(current: JsonSchema | list[JsonSchema]) -> None:
        """Recursively process and bundle references in the current schema."""
        if isinstance(current, dict):
            ref = current.get("$ref")
            if isinstance(ref, str) and not ref.startswith(REFERENCE_TO_BUNDLE_PREFIX):
                resolved_uri, resolved_schema = resolver.resolve(ref)
                def_name = get_def_name(resolved_uri)

                # Bundle only new schemas
                if resolved_uri not in visited:
                    visited.add(resolved_uri)

                    cloned = deepclone(resolved_schema)
                    defs[def_name] = cloned

                    # Recursively bundle the embedded schema too!
                    resolver.push_scope(resolved_uri)
                    try:
                        bundle_recursive(cloned)
                    finally:
                        resolver.pop_scope()

                # Update reference to point to embedded definition
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
