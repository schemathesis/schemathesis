from collections.abc import Mapping
from typing import Any, TypeGuard

import jsonschema_rs
from jsonschema_rs import canonical

from schemathesis.core.jsonschema import FANCY_REGEX_OPTIONS
from schemathesis.specs.openapi.negative.types import Schema


def can_negate(schema: Schema) -> bool:
    """Whether the schema rejects anything at all, so that a value violating it exists."""
    try:
        canonical_schema = jsonschema_rs.canonicalize(
            schema,
            draft=jsonschema_rs.Draft202012,
            pattern_options=FANCY_REGEX_OPTIONS,
            validate_formats=True,
        )
    except (jsonschema_rs.ValidationError, jsonschema_rs.canonical.CanonicalizationError):
        # Refusing here would drop the parameter from negative testing; a mutation that turns out
        # valid is discarded downstream anyway.
        return True
    return not isinstance(canonical_schema.view(), canonical.TrueView)


def is_binary_format(schema: object) -> TypeGuard[Mapping[str, Any]]:
    """Check if schema is a permissive binary format that accepts any bytes."""
    if not isinstance(schema, Mapping):
        return False
    return schema.get("type") == "string" and schema.get("format") in ("binary", "byte")
