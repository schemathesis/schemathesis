from __future__ import annotations

from typing import Final

import jsonschema_rs

from schemathesis.core.cache import MISSING, BoundedCache
from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY
from schemathesis.core.jsonschema.types import JsonValue

# Every sub-schema of an operation carries the same bundle, so cache keys share one copy of its text.
bundle_text_cache: Final[BoundedCache] = BoundedCache(maxsize=16)


def schema_cache_key(schema: JsonValue) -> tuple[str, ...]:
    if isinstance(schema, dict):
        bundle = schema.get(BUNDLE_STORAGE_KEY)
        if bundle is not None:
            without_bundle = {k: v for k, v in schema.items() if k != BUNDLE_STORAGE_KEY}
            return (
                "dict_with_bundle",
                jsonschema_rs.canonical.json.to_string(without_bundle),
                _shared_bundle_text(jsonschema_rs.canonical.json.to_string(bundle)),
            )
        return ("dict", jsonschema_rs.canonical.json.to_string(schema))
    return ("json", jsonschema_rs.canonical.json.to_string(schema))


def _shared_bundle_text(text: str) -> str:
    shared = bundle_text_cache.get(text)
    if shared is MISSING:
        bundle_text_cache[text] = text
        return text
    return shared
