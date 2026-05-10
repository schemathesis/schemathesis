from __future__ import annotations

import jsonschema_rs

from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY
from schemathesis.core.jsonschema.types import JsonValue


def schema_cache_key(schema: JsonValue) -> tuple[str, ...]:
    if isinstance(schema, dict):
        bundle = schema.get(BUNDLE_STORAGE_KEY)
        if bundle is not None:
            without_bundle = {k: v for k, v in schema.items() if k != BUNDLE_STORAGE_KEY}
            return (
                "dict_with_bundle",
                jsonschema_rs.canonical.json.to_string(without_bundle),
                jsonschema_rs.canonical.json.to_string(bundle),
            )
        return ("dict", jsonschema_rs.canonical.json.to_string(schema))
    return ("json", jsonschema_rs.canonical.json.to_string(schema))
