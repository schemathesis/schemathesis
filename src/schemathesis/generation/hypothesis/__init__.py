from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Literal


def setup() -> None:
    import jsonschema_rs
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
    from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY
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

        __slots__ = ("schema", "encoded", "serialized")

        def __init__(self, schema: dict[str, Any]) -> None:
            self.schema = schema
            bundle = schema.get(BUNDLE_STORAGE_KEY)
            if bundle is not None:
                _for_hash = {k: v for k, v in schema.items() if k != BUNDLE_STORAGE_KEY}
                self.serialized = json.dumps(_for_hash, sort_keys=True)
                self.encoded = hash((self.serialized, id(bundle)))
            else:
                self.serialized = json.dumps(schema, sort_keys=True)
                self.encoded = hash(self.serialized)

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

    # Cache for fully-resolved schema output, keyed by schema hash.
    # Avoids re-traversing schemas with the same JSON content.
    _resolve_result_cache: dict[int, dict[str, Any]] = {}

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

        _resolve_all_refs = resolve_all_refs
        top_level = resolver is None
        schema_hash: int | None = None

        if top_level:
            cache_key = CacheableSchema(schema)
            # No need to traverse if there are no references
            if '"$ref"' not in cache_key.serialized:
                return schema
            schema_hash = cache_key.encoded
            if schema_hash in _resolve_result_cache:
                return deepclone(_resolve_result_cache[schema_hash])
            resolver = get_resolver(cache_key)

        assert resolver is not None

        if "$ref" in schema:
            s = dict(schema)
            ref = s.pop("$ref")
            url, resolved = resolver.resolve(ref)
            resolver.push_scope(url)
            try:
                result = merged(
                    [_resolve_all_refs(s, resolver=resolver), _resolve_all_refs(deepclone(resolved), resolver=resolver)]
                )
            finally:
                resolver.pop_scope()
            if schema_hash is not None:
                _resolve_result_cache[schema_hash] = deepclone(result)
            return result

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
        if schema_hash is not None:
            _resolve_result_cache[schema_hash] = deepclone(schema)
        return schema

    root_core.RepresentationPrinter = RepresentationPrinter
    _resolve.deepcopy = deepclone
    _resolve.resolve_all_refs = resolve_all_refs
    _from_schema.deepcopy = deepclone
    _from_schema.get_type = _get_type
    _from_schema.resolve_all_refs = resolve_all_refs
    _canonicalise.get_type = _get_type
    _canonicalise.CacheableSchema = CacheableSchema

    # Patch canonicalish to skip x-bundled during the deep-copy serialisation.
    _original_canonicalish = _canonicalise.canonicalish

    def _fast_canonicalish(schema: Any) -> dict[str, Any]:
        if not isinstance(schema, dict) or BUNDLE_STORAGE_KEY not in schema:
            return _original_canonicalish(schema)
        bundle = schema[BUNDLE_STORAGE_KEY]
        schema_without_bundle = {k: v for k, v in schema.items() if k != BUNDLE_STORAGE_KEY}
        result = _original_canonicalish(schema_without_bundle)
        # Restore x-bundled so downstream $ref resolution can find bundled schemas.
        if isinstance(result, dict) and result and result != {"not": {}}:
            result[BUNDLE_STORAGE_KEY] = bundle
        return result

    _canonicalise.canonicalish = _fast_canonicalish
    _from_schema.canonicalish = _fast_canonicalish
    _resolve.canonicalish = _fast_canonicalish
    root_core.BUFFER_SIZE = INTERNAL_BUFFER_SIZE
    engine.BUFFER_SIZE = INTERNAL_BUFFER_SIZE
    collections.BUFFER_SIZE = INTERNAL_BUFFER_SIZE

    # Patch make_validator to use jsonschema-rs for instance validation
    from schemathesis.transport.serialization import Binary

    _original_get_validator_class = _canonicalise._get_validator_class

    def _contains_binary(value: Any) -> bool:
        if isinstance(value, Binary):
            return True
        if isinstance(value, dict):
            return any(_contains_binary(v) for v in value.values())
        if isinstance(value, list):
            return any(_contains_binary(v) for v in value)
        return False

    class _ValidatorWrapper:
        __slots__ = ("_validator",)

        def __init__(self, validator: Any) -> None:
            self._validator = validator

        def is_valid(self, value: Any) -> bool:
            if _contains_binary(value):
                return True
            return self._validator.is_valid(value)

    def make_validator(schema: dict[str, Any]) -> _ValidatorWrapper:
        try:
            return _ValidatorWrapper(jsonschema_rs.validator_for(schema))
        except (jsonschema_rs.ValidationError, ValueError, TypeError):
            cls = _original_get_validator_class(schema)
            return _ValidatorWrapper(cls(schema))

    def _get_validator_class(schema: dict[str, Any]) -> Any:
        try:
            jsonschema_rs.meta.validate(schema)
            return jsonschema_rs.validator_cls_for(schema)
        except (jsonschema_rs.ValidationError, ValueError, TypeError):
            return _original_get_validator_class(schema)

    _canonicalise.make_validator = make_validator
    _from_schema.make_validator = make_validator
    _canonicalise._get_validator_class = _get_validator_class
