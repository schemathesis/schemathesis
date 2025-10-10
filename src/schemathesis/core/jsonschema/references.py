from __future__ import annotations

from typing import Any

from schemathesis.core.jsonschema.types import JsonSchema, JsonSchemaObject


def sanitize(schema: JsonSchema) -> set[str]:
    """Remove $ref from optional locations."""
    if isinstance(schema, bool):
        return set()

    stack: list[JsonSchema] = [schema]

    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue

        _sanitize_combinators(current)

        _sanitize_properties(current)

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

    remaining: set[str] = set()
    _collect_all_references(schema, remaining)
    return remaining


def _sanitize_combinators(schema: JsonSchemaObject) -> None:
    """Sanitize anyOf/oneOf/allOf."""
    for combinator_key in ("anyOf", "oneOf"):
        variants = schema.get(combinator_key)
        if not isinstance(variants, list):
            continue

        flattened = _flatten_combinator(variants, combinator_key)

        cleaned = [variant for variant in flattened if not _has_ref(variant)]

        # Only update if we have non-$ref variants
        if cleaned:
            # At least one alternative remains, which narrows the constraints
            schema[combinator_key] = cleaned
        elif not flattened:
            schema.pop(combinator_key, None)

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        flattened = _flatten_combinator(all_of, "allOf")

        cleaned = [variant for variant in flattened if not _is_empty(variant)]
        if cleaned:
            schema["allOf"] = cleaned
        else:
            schema.pop("allOf", None)


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
