from __future__ import annotations

from itertools import chain
from typing import Any, Callable

from schemathesis.core.transforms import deepclone, transform

from .patterns import update_quantifier


def to_json_schema(
    schema: dict[str, Any],
    *,
    nullable_name: str,
    copy: bool = True,
    is_response_schema: bool = False,
    update_quantifiers: bool = True,
) -> dict[str, Any]:
    """Convert Open API parameters to JSON Schema.

    NOTE. This function is applied to all keywords (including nested) during a schema resolving, thus it is not recursive.
    See a recursive version below.
    """
    if copy:
        schema = deepclone(schema)
    if schema.get(nullable_name) is True:
        del schema[nullable_name]
        schema = {"anyOf": [schema, {"type": "null"}]}
    schema_type = schema.get("type")
    if schema_type == "file":
        schema["type"] = "string"
        schema["format"] = "binary"
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
    return schema


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
    forbidden = []
    for name, subschema in list(schema.get("properties", {}).items()):
        if predicate(subschema):
            if name in required:
                required.remove(name)
            del schema["properties"][name]
            forbidden.append(name)
    if forbidden:
        forbid_properties(schema, forbidden)
    if not schema.get("required"):
        schema.pop("required", None)
    if not schema.get("properties"):
        schema.pop("properties", None)


def forbid_properties(schema: dict[str, Any], forbidden: list[str]) -> None:
    """Explicitly forbid properties via the `not` keyword."""
    not_schema = schema.setdefault("not", {})
    already_forbidden = not_schema.setdefault("required", [])
    already_forbidden.extend(forbidden)
    not_schema["required"] = list(set(chain(already_forbidden, forbidden)))


def is_write_only(schema: dict[str, Any] | bool) -> bool:
    if isinstance(schema, bool):
        return False
    return schema.get("writeOnly", False) or schema.get("x-writeOnly", False)


def is_read_only(schema: dict[str, Any] | bool) -> bool:
    if isinstance(schema, bool):
        return False
    return schema.get("readOnly", False)


def to_json_schema_recursive(
    schema: dict[str, Any], nullable_name: str, is_response_schema: bool = False, update_quantifiers: bool = True
) -> dict[str, Any]:
    return transform(
        schema,
        to_json_schema,
        nullable_name=nullable_name,
        is_response_schema=is_response_schema,
        update_quantifiers=update_quantifiers,
    )
