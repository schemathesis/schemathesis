from __future__ import annotations

from typing import Callable

from .config import TransformConfig
from .types import ObjectSchema, Schema


def transform_schema(schema: ObjectSchema, config: TransformConfig) -> None:
    """Replace all Open API specific keywords with their JSON Schema equivalents."""
    type_ = schema.get("type")
    if type_ == "file":
        _replace_file_type(schema)
    elif type_ == "object":
        if config.remove_write_only:
            # Write-only properties should not occur in responses
            _rewrite_properties(schema, _is_write_only)
        if config.remove_read_only:
            # Read-only properties should not occur in requests
            _rewrite_properties(schema, _is_read_only)
    if schema.get(config.nullable_key) is True:
        _replace_nullable(schema, config.nullable_key)


def _replace_file_type(item: ObjectSchema) -> None:
    item["type"] = "string"
    item["format"] = "binary"


def _rewrite_properties(schema: ObjectSchema, predicate: Callable[[ObjectSchema], bool]) -> None:
    required = schema.get("required", [])
    forbidden = []
    for name, subschema in list(schema.get("properties", {}).items()):
        if predicate(subschema):
            if name in required:
                required.remove(name)
            del schema["properties"][name]
            forbidden.append(name)
    if forbidden:
        _forbid_properties(schema, forbidden)
    if not schema.get("required"):
        schema.pop("required", None)
    if not schema.get("properties"):
        schema.pop("properties", None)


def _forbid_properties(schema: ObjectSchema, forbidden: list[str]) -> None:
    """Explicitly forbid properties via the `not` keyword."""
    not_schema = schema.setdefault("not", {})
    already_forbidden = not_schema.setdefault("required", [])
    already_forbidden.extend(forbidden)
    not_schema["required"] = list(set(already_forbidden))


def _is_write_only(schema: Schema) -> bool:
    if isinstance(schema, bool):
        return False
    return schema.get("writeOnly", False) or schema.get("x-writeOnly", False)


def _is_read_only(schema: Schema) -> bool:
    if isinstance(schema, bool):
        return False
    return schema.get("readOnly", False)


def _replace_nullable(item: ObjectSchema, nullable_key: str) -> None:
    del item[nullable_key]
    # Move all other keys to a new object, except for `x-moved-references` which should
    # always be at the root level
    inner = {}
    for key, value in list(item.items()):
        inner[key] = value
        del item[key]
    item["anyOf"] = [inner, {"type": "null"}]
