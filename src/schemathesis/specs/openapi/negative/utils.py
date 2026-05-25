from collections.abc import Mapping
from typing import Any, TypeGuard

import jsonschema_rs

from schemathesis.specs.openapi.negative.types import Schema


def can_negate(schema: Schema) -> bool:
    # A schema that accepts everything canonicalizes to the universal schema (`True`/`{}`) and has no negatives.
    try:
        return jsonschema_rs.canonicalize(schema).to_json_schema() not in (True, {})
    except ValueError:
        return True


def is_binary_format(schema: object) -> TypeGuard[Mapping[str, Any]]:
    """Check if schema is a permissive binary format that accepts any bytes."""
    if not isinstance(schema, Mapping):
        return False
    return schema.get("type") == "string" and schema.get("format") in ("binary", "byte")
