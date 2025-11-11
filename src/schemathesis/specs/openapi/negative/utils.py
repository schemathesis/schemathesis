from typing import Any

from hypothesis_jsonschema._canonicalise import canonicalish

from .types import Schema


def can_negate(schema: Schema) -> bool:
    return canonicalish(schema) != {}


def is_binary_format(schema: dict[str, Any]) -> bool:
    """Check if schema is a permissive binary format that accepts any bytes."""
    return schema.get("type") == "string" and schema.get("format") in ("binary", "byte")
