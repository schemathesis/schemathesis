from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Literal


def setup() -> None:
    from hypothesis import core as root_core
    from hypothesis.internal.conjecture import engine
    from hypothesis.internal.entropy import deterministic_PRNG
    from hypothesis.internal.reflection import is_first_param_referenced_in_function
    from hypothesis.strategies._internal import collections, core
    from hypothesis.vendor import pretty
    from hypothesis_jsonschema import _canonicalise, _from_schema, _resolve
    from hypothesis_jsonschema._canonicalise import SCHEMA_KEYS, SCHEMA_OBJECT_KEYS, merged
    from hypothesis_jsonschema._resolve import LocalResolver

    from schemathesis.core import INTERNAL_BUFFER_SIZE
    from schemathesis.core.jsonschema.types import _get_type
    from schemathesis.core.transforms import deepclone

    # Forcefully initializes Hypothesis' global PRNG to avoid races that initialize it
    # if e.g. Schemathesis CLI is used with multiple workers
    with deterministic_PRNG():
        pass

    # A set of performance-related patches

    # This one is used a lot, and under the hood it re-parses the AST of the same function
    def _is_first_param_referenced_in_function(f: Any) -> bool:
        if f.__name__ == "from_object_schema" and f.__module__ == "hypothesis_jsonschema._from_schema":
            return True
        return is_first_param_referenced_in_function(f)

    core.is_first_param_referenced_in_function = _is_first_param_referenced_in_function

    class RepresentationPrinter(pretty.RepresentationPrinter):
        def pretty(self, obj: object) -> None:
            # This one takes way too much - in the coverage phase it may give >2 orders of magnitude improvement
            # depending on the schema size (~300 seconds -> 4.5 seconds in one of the benchmarks)
            return None

    class CacheableSchema:
        """Cache schema by its JSON representation.

        Canonicalisation is not required as schemas with the same JSON representation
        will have the same validator.
        """

        __slots__ = ("schema", "encoded")

        def __init__(self, schema: dict[str, Any]) -> None:
            self.schema = schema
            self.encoded = hash(json.dumps(schema, sort_keys=True))

        def __eq__(self, other: CacheableSchema) -> bool:  # type: ignore[override]
            return self.encoded == other.encoded

        def __hash__(self) -> int:
            return self.encoded

    SCHEMA_KEYS = frozenset(SCHEMA_KEYS)
    SCHEMA_OBJECT_KEYS = frozenset(SCHEMA_OBJECT_KEYS)

    @lru_cache
    def get_resolver(cache_key: CacheableSchema) -> LocalResolver:
        """LRU resolver cache."""
        return LocalResolver.from_schema(cache_key.schema)

    def resolve_all_refs(
        schema: Literal[True, False] | dict[str, Any],
        *,
        resolver: LocalResolver | None = None,
    ) -> dict[str, Any]:
        if schema is True:
            return {}
        if schema is False:
            return {"not": {}}
        if not schema:
            return schema
        if resolver is None:
            resolver = get_resolver(CacheableSchema(schema))

        _resolve_all_refs = resolve_all_refs

        if "$ref" in schema:
            s = dict(schema)
            ref = s.pop("$ref")
            url, resolved = resolver.resolve(ref)
            resolver.push_scope(url)
            try:
                return merged(
                    [_resolve_all_refs(s, resolver=resolver), _resolve_all_refs(deepclone(resolved), resolver=resolver)]
                )
            finally:
                resolver.pop_scope()

        for key, value in schema.items():
            if key in SCHEMA_KEYS:
                if isinstance(value, list):
                    schema[key] = [_resolve_all_refs(v, resolver=resolver) if isinstance(v, dict) else v for v in value]
                elif isinstance(value, dict):
                    schema[key] = _resolve_all_refs(value, resolver=resolver)
            if key in SCHEMA_OBJECT_KEYS:
                schema[key] = {
                    k: _resolve_all_refs(v, resolver=resolver) if isinstance(v, dict) else v for k, v in value.items()
                }
        return schema

    root_core.RepresentationPrinter = RepresentationPrinter
    _resolve.deepcopy = deepclone
    _resolve.resolve_all_refs = resolve_all_refs
    _from_schema.deepcopy = deepclone
    _from_schema.get_type = _get_type
    _from_schema.resolve_all_refs = resolve_all_refs
    _canonicalise.get_type = _get_type
    _canonicalise.CacheableSchema = CacheableSchema
    root_core.BUFFER_SIZE = INTERNAL_BUFFER_SIZE
    engine.BUFFER_SIZE = INTERNAL_BUFFER_SIZE
    collections.BUFFER_SIZE = INTERNAL_BUFFER_SIZE
