from collections.abc import Mapping
from typing import Any, TypeGuard

from hypothesis_jsonschema._canonicalise import canonicalish

from .types import Schema


def can_negate(schema: Schema) -> bool:
    return canonicalish(schema) != {}


def is_binary_format(schema: object) -> TypeGuard[Mapping[str, Any]]:
    """Check if schema is a permissive binary format that accepts any bytes."""
    if not isinstance(schema, Mapping):
        return False
    return schema.get("type") == "string" and schema.get("format") in ("binary", "byte")
