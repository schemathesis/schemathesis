from __future__ import annotations

from collections.abc import Callable
from typing import Any, overload

from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.core.transforms import deepclone
from schemathesis.specs.openapi.patterns import is_valid_python_regex, update_quantifier


@overload
def to_json_schema(
    schema: dict[str, Any],
    nullable_keyword: str,
    is_response_schema: bool = False,
    update_quantifiers: bool = True,
    clone: bool = True,
) -> dict[str, Any]: ...  # pragma: no cover


@overload
def to_json_schema(
    schema: bool,
    nullable_keyword: str,
    is_response_schema: bool = False,
    update_quantifiers: bool = True,
    clone: bool = True,
) -> bool: ...  # pragma: no cover


def to_json_schema(
    schema: dict[str, Any] | bool,
    nullable_keyword: str,
    is_response_schema: bool = False,
    update_quantifiers: bool = True,
    clone: bool = True,
) -> dict[str, Any] | bool:
    if isinstance(schema, bool):
        return schema
    if clone:
        schema = deepclone(schema)
    return _to_json_schema(
        schema,
        nullable_keyword=nullable_keyword,
        is_response_schema=is_response_schema,
        update_quantifiers=update_quantifiers,
    )


def _to_json_schema(
    schema: JsonSchema,
    *,
    nullable_keyword: str,
    is_response_schema: bool = False,
    update_quantifiers: bool = True,
) -> JsonSchema:
    if isinstance(schema, bool):
        return schema

    if schema.get(nullable_keyword) is True:
        del schema[nullable_keyword]
        bundled = schema.pop(BUNDLE_STORAGE_KEY, None)
        schema = {"anyOf": [schema, {"type": "null"}]}
        if bundled:
            schema[BUNDLE_STORAGE_KEY] = bundled
    schema_type = schema.get("type")
    if schema_type == "file":
        schema["type"] = "string"
        schema["format"] = "binary"
    # Remove unsupported regex patterns
    pattern = schema.get("pattern")
    if pattern is not None and not is_valid_python_regex(pattern):
        del schema["pattern"]
    if update_quantifiers:
        update_pattern_in_schema(schema)
    # Sometimes `required` is incorrectly has a boolean value
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, subschema in properties.items():
            if not isinstance(subschema, dict):
                continue
            is_required = subschema.get("required")
            if is_required is True:
                schema.setdefault("required", []).append(name)
                del subschema["required"]
            elif is_required is False:
                if "required" in schema and name in schema["required"]:
                    schema["required"].remove(name)
                del subschema["required"]

    if schema_type == "object":
        if is_response_schema:
            # Write-only properties should not occur in responses
            rewrite_properties(schema, is_write_only)
        else:
            # Read-only properties should not occur in requests
            rewrite_properties(schema, is_read_only)

    ensure_required_properties(schema)

    # Convert JSON Schema Draft 2020-12 prefixItems to Draft 4/7 items array form
    # hypothesis-jsonschema only supports Draft 4/6/7
    if "prefixItems" in schema:
        prefix_items = schema.pop("prefixItems")
        if "items" in schema:
            # When both prefixItems and items exist, items becomes additionalItems
            schema["additionalItems"] = schema.pop("items")
        schema["items"] = prefix_items

    for keyword, value in schema.items():
        if keyword in IN_VALUE and isinstance(value, dict):
            schema[keyword] = _to_json_schema(
                value,
                nullable_keyword=nullable_keyword,
                is_response_schema=is_response_schema,
                update_quantifiers=update_quantifiers,
            )
        elif keyword in IN_ITEM and isinstance(value, list):
            for idx, subschema in enumerate(value):
                value[idx] = _to_json_schema(
                    subschema,
                    nullable_keyword=nullable_keyword,
                    is_response_schema=is_response_schema,
                    update_quantifiers=update_quantifiers,
                )
        elif keyword in IN_CHILD and isinstance(value, dict):
            for name, subschema in value.items():
                value[name] = _to_json_schema(
                    subschema,
                    nullable_keyword=nullable_keyword,
                    is_response_schema=is_response_schema,
                    update_quantifiers=update_quantifiers,
                )

    return schema


def ensure_required_properties(schema: dict[str, Any]) -> None:
    if schema.get("additionalProperties") is not False:
        return

    required = schema.get("required")
    if not required or not isinstance(required, list):
        return

    properties = schema.setdefault("properties", {})

    # Add missing required properties as empty schemas
    for name in required:
        if name not in properties:
            properties[name] = {}


IN_VALUE = frozenset(
    (
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    )
)
IN_ITEM = frozenset(
    (
        "allOf",
        "anyOf",
        "oneOf",
    )
)
IN_CHILD = frozenset(
    (
        "$defs",
        "definitions",
        "dependentSchemas",
        "patternProperties",
        "properties",
        BUNDLE_STORAGE_KEY,
    )
)


def update_pattern_in_schema(schema: dict[str, Any]) -> None:
    pattern = schema.get("pattern")
    min_length = schema.get("minLength")
    max_length = schema.get("maxLength")
    if pattern and (min_length or max_length):
        new_pattern = update_quantifier(pattern, min_length, max_length)
        if new_pattern != pattern:
            schema.pop("minLength", None)
            schema.pop("maxLength", None)
            schema["pattern"] = new_pattern


def rewrite_properties(schema: dict[str, Any], predicate: Callable[[dict[str, Any]], bool]) -> None:
    required = schema.get("required", [])
    for name, subschema in list(schema.get("properties", {}).items()):
        if predicate(subschema):
            if name in required:
                required.remove(name)
            schema["properties"][name] = {"not": {}}
    if not schema.get("required"):
        schema.pop("required", None)
    if not schema.get("properties"):
        schema.pop("properties", None)


def is_write_only(schema: dict[str, Any] | bool) -> bool:
    if isinstance(schema, bool):
        return False
    return schema.get("writeOnly", False) or schema.get("x-writeOnly", False)


def is_read_only(schema: dict[str, Any] | bool) -> bool:
    if isinstance(schema, bool):
        return False
    return schema.get("readOnly", False)
