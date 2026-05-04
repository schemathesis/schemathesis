from __future__ import annotations

from collections.abc import Callable
from typing import Any

from schemathesis.core.jsonschema.types import JsonSchema, JsonSchemaObject


def prune_optional_refs(schema: JsonSchema, *, is_recursive_ref: Callable[[str], bool] | None = None) -> None:
    """Remove $ref from optional locations, mutating `schema` in place.

    `is_recursive_ref`, if provided, returns ``True`` for $ref strings that point back
    to a schema currently being inlined. Such refs are dropped from `oneOf`/`anyOf`
    variants when other variants remain, and from the *top-level* `allOf` of the
    inlined schema (where `{$ref: S}` is trivially satisfied — but only there;
    nested it's a real constraint on a sub-value).
    """
    if isinstance(schema, bool):
        return

    if is_recursive_ref is not None and isinstance(schema, dict):
        _drop_recursive_top_level_allof(schema, is_recursive_ref)

    stack: list[JsonSchema] = [schema]

    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue

        # `definitions` / `$defs` are storage for ref targets, not validation constraints.
        # Active refs still resolve through the resolver, so dropping these blocks avoids
        # walking $refs that have already been pruned from optional positions.
        current.pop("definitions", None)
        current.pop("$defs", None)

        _sanitize_combinators(current, is_recursive_ref)

        _sanitize_properties(current)

        if "patternProperties" in current:
            _sanitize_pattern_properties(current)

        if "items" in current:
            _sanitize_items(current)

        if "prefixItems" in current:
            _sanitize_prefix_items(current)

        if "additionalProperties" in current:
            _sanitize_additional_properties(current)

        if "additionalItems" in current:
            _sanitize_additional_items(current)

        for value in current.values():
            if isinstance(value, dict):
                stack.append(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        stack.append(item)


def collect_all_references(schema: JsonSchema | list[JsonSchema]) -> set[str]:
    """Return every `$ref` value found anywhere in `schema`."""
    remaining: set[str] = set()
    _collect_all_references(schema, remaining)
    return remaining


def _drop_recursive_top_level_allof(schema: JsonSchemaObject, is_recursive_ref: Callable[[str], bool]) -> None:
    all_of = schema.get("allOf")
    if not isinstance(all_of, list):
        return
    kept = [entry for entry in all_of if not _is_self_ref(entry, is_recursive_ref)]
    if kept:
        schema["allOf"] = kept
    else:
        schema.pop("allOf", None)


def _sanitize_combinators(schema: JsonSchemaObject, is_recursive_ref: Callable[[str], bool] | None = None) -> None:
    """Sanitize anyOf/oneOf/allOf."""
    for combinator_key in ("anyOf", "oneOf"):
        variants = schema.get(combinator_key)
        if not isinstance(variants, list):
            continue

        flattened = _flatten_combinator(variants, combinator_key)

        # Drop variants that are pure refs back to the schema being inlined, when at
        # least one usable variant remains. Kept variants are part of the original
        # alternatives, so generated data still satisfies the original `oneOf`/`anyOf`.
        if is_recursive_ref is not None:
            non_recursive = [v for v in flattened if not _is_self_ref(v, is_recursive_ref)]
            if non_recursive and len(non_recursive) < len(flattened):
                flattened = non_recursive

        cleaned = [variant for variant in flattened if not _has_ref(variant)]

        # Only update if we have non-$ref variants
        if cleaned:
            # At least one alternative remains, which narrows the constraints
            schema[combinator_key] = cleaned
        elif not flattened:
            schema.pop(combinator_key, None)
        else:
            schema[combinator_key] = flattened

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        flattened = _flatten_combinator(all_of, "allOf")

        cleaned = [variant for variant in flattened if not _is_empty(variant)]
        if cleaned:
            schema["allOf"] = cleaned
        else:
            schema.pop("allOf", None)


def _is_self_ref(entry: Any, is_recursive_ref: Callable[[str], bool]) -> bool:
    """A bare `{"$ref": X}` whose target is currently being inlined."""
    if not isinstance(entry, dict) or list(entry) != ["$ref"]:
        return False
    return isinstance(entry["$ref"], str) and is_recursive_ref(entry["$ref"])


def _flatten_combinator(variants: list, key: str) -> list:
    """Flatten nested same-type combinators."""
    result = []
    for variant in variants:
        if isinstance(variant, dict) and key in variant and isinstance(variant[key], list):
            result.extend(variant[key])
        else:
            result.append(variant)
    return result


def _is_empty(schema: JsonSchema) -> bool:
    """Check if schema accepts anything."""
    if schema is True:
        return True

    if not isinstance(schema, dict):
        return False

    if not schema:
        return True

    # Only non-validating keywords
    NON_VALIDATING = {
        "$id",
        "$schema",
        "$defs",
        "definitions",
        "title",
        "description",
        "default",
        "examples",
        "example",
        "$comment",
        "deprecated",
        "readOnly",
        "writeOnly",
    }

    return all(key in NON_VALIDATING for key in schema.keys())


def _sanitize_properties(schema: JsonSchemaObject) -> None:
    """Remove OPTIONAL property schemas if they have $ref."""
    if "properties" not in schema:
        return

    properties = schema["properties"]
    if not isinstance(properties, dict):
        return

    required = schema.get("required", [])

    for name, subschema in list(properties.items()):
        if not _has_ref(subschema):
            continue

        if name not in required:
            del properties[name]


def _sanitize_pattern_properties(schema: JsonSchemaObject) -> None:
    """Drop `patternProperties` entries whose value contains a `$ref`.

    Each entry is structurally optional: an object with no key matching the
    regex satisfies the schema regardless of the entry's sub-schema. Removing
    ref-bearing entries breaks recursive cycles without forcing the bundler
    to inline another level. The original schema still applies at validation
    time — schemathesis just won't generate matching keys itself.
    """
    pattern_properties = schema["patternProperties"]
    if not isinstance(pattern_properties, dict):
        return
    for key, subschema in list(pattern_properties.items()):
        if _has_ref(subschema):
            del pattern_properties[key]


def _sanitize_items(schema: JsonSchemaObject) -> None:
    """Convert to empty array ONLY if minItems allows it."""
    items = schema["items"]

    has_ref = False
    if isinstance(items, dict):
        has_ref = _has_ref(items)
    elif isinstance(items, list):
        has_ref = any(_has_ref(item) for item in items)

    if not has_ref:
        return

    min_items = schema.get("minItems", 0)

    if min_items == 0:
        _convert_to_empty_array(schema)


def _sanitize_prefix_items(schema: JsonSchemaObject) -> None:
    """Same logic as items."""
    prefix_items = schema["prefixItems"]

    if not isinstance(prefix_items, list):
        return

    if not any(_has_ref(item) for item in prefix_items):
        return

    min_items = schema.get("minItems", 0)

    if min_items == 0:
        _convert_to_empty_array(schema)


def _convert_to_empty_array(schema: JsonSchemaObject) -> None:
    schema.pop("items", None)
    schema.pop("prefixItems", None)
    schema["maxItems"] = 0
    schema["minItems"] = 0


def _sanitize_additional_properties(schema: JsonSchemaObject) -> None:
    additional = schema["additionalProperties"]
    if _has_ref(additional):
        schema["additionalProperties"] = False


def _sanitize_additional_items(schema: JsonSchemaObject) -> None:
    additional = schema["additionalItems"]
    if _has_ref(additional):
        schema["additionalItems"] = False


def _has_ref(schema: Any) -> bool:
    """Check if schema contains $ref at any level."""
    if not isinstance(schema, dict):
        return False

    if "$ref" in schema:
        return True
    for value in schema.values():
        if isinstance(value, dict):
            if _has_ref(value):
                return True
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and _has_ref(item):
                    return True

    return False


def _collect_all_references(schema: JsonSchema | list[JsonSchema], remaining: set[str]) -> None:
    """Collect all remaining $ref."""
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str):
            remaining.add(ref)
        for value in schema.values():
            _collect_all_references(value, remaining)
    elif isinstance(schema, list):
        for item in schema:
            _collect_all_references(item, remaining)
