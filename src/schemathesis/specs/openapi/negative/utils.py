from collections.abc import Mapping
from typing import Any, TypeGuard

import jsonschema_rs
from jsonschema_rs import canonical

from schemathesis.core.cache import MISSING
from schemathesis.core.jsonschema import FANCY_REGEX_OPTIONS
from schemathesis.generation._cache import schema_cache_key
from schemathesis.generation.hypothesis import canonical_form_cache
from schemathesis.specs.openapi.negative.types import Schema

_DRAFT: int = jsonschema_rs.Draft202012


def can_negate(schema: Schema) -> bool:
    """Whether the schema rejects anything at all, so that a value violating it exists."""
    # A rejection-filter retry re-draws the same mutated schema many times; this cache is shared
    # with strategy building, so a schema canonicalized there is free here too (and vice versa).
    try:
        schema_key = schema_cache_key(schema)
    except (TypeError, ValueError):
        schema_key = None
    key = (schema_key, _DRAFT) if schema_key is not None else None
    if key is not None:
        cached = canonical_form_cache.get(key)
        if cached is not MISSING:
            return not isinstance(cached.view(), canonical.TrueView)
    try:
        canonical_schema = jsonschema_rs.canonicalize(
            schema,
            draft=_DRAFT,
            pattern_options=FANCY_REGEX_OPTIONS,
            validate_formats=True,
        )
    except (jsonschema_rs.ValidationError, jsonschema_rs.canonical.CanonicalizationError):
        # Refusing here would drop the parameter from negative testing; a mutation that turns out
        # valid is discarded downstream anyway.
        return True
    if key is not None:
        canonical_form_cache[key] = canonical_schema
    return not isinstance(canonical_schema.view(), canonical.TrueView)


def is_binary_format(schema: object) -> TypeGuard[Mapping[str, Any]]:
    """Check if schema is a permissive binary format that accepts any bytes."""
    if not isinstance(schema, Mapping):
        return False
    return schema.get("type") == "string" and schema.get("format") in ("binary", "byte")
