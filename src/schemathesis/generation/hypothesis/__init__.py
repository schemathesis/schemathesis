from __future__ import annotations

from collections import OrderedDict
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
    from hypothesis_jsonschema import _canonicalise, _encode, _from_schema, _resolve
    from hypothesis_jsonschema._canonicalise import SCHEMA_KEYS, SCHEMA_OBJECT_KEYS
    from hypothesis_jsonschema._canonicalise import merged as _original_merged
    from hypothesis_jsonschema._resolve import LocalResolver

    from schemathesis.core import INTERNAL_BUFFER_SIZE
    from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY, FANCY_REGEX_OPTIONS, REFERENCE_TO_BUNDLE_PREFIX
    from schemathesis.core.jsonschema.types import _get_type
    from schemathesis.core.transforms import deepclone

    if getattr(setup, "_is_patched", False):
        return

    # Forcefully initializes Hypothesis' global PRNG to avoid races that initialize it
    # if e.g. Schemathesis CLI is used with multiple workers
    with deterministic_PRNG():
        pass

    # A set of performance-related patches
    _encode.encode_canonical_json = jsonschema_rs.canonical.json.to_string
    _canonicalise.encode_canonical_json = jsonschema_rs.canonical.json.to_string
    _from_schema.encode_canonical_json = jsonschema_rs.canonical.json.to_string

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
                self.serialized = jsonschema_rs.canonical.json.to_string(_for_hash)
                self.encoded = hash((self.serialized, id(bundle)))
            else:
                self.serialized = jsonschema_rs.canonical.json.to_string(schema)
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
    _merged_result_cache: OrderedDict[tuple[tuple[Any, ...], tuple[Any, ...]], dict[str, Any] | None] = OrderedDict()
    _merged_result_cache_maxsize = 4096

    def _schema_cache_key(schema: Any) -> tuple[Any, ...]:
        if isinstance(schema, dict):
            bundle = schema.get(BUNDLE_STORAGE_KEY)
            if bundle is not None:
                without_bundle = {k: v for k, v in schema.items() if k != BUNDLE_STORAGE_KEY}
                serialized = jsonschema_rs.canonical.json.to_string(without_bundle)
                return ("dict_with_bundle", serialized, id(bundle))
            return ("dict", jsonschema_rs.canonical.json.to_string(schema))
        return ("json", jsonschema_rs.canonical.json.to_string(schema))

    def _merge_cache_get(key: tuple[tuple[Any, ...], tuple[Any, ...]]) -> dict[str, Any] | None | Literal[False]:
        if key in _merged_result_cache:
            _merged_result_cache.move_to_end(key)
            cached = _merged_result_cache[key]
            if cached is None:
                return None
            return deepclone(cached)
        return False

    def _merge_cache_set(key: tuple[tuple[Any, ...], tuple[Any, ...]], value: dict[str, Any] | None) -> None:
        _merged_result_cache[key] = deepclone(value) if isinstance(value, dict) else None
        _merged_result_cache.move_to_end(key)
        if len(_merged_result_cache) > _merged_result_cache_maxsize:
            _merged_result_cache.popitem(last=False)

    def _is_trivial_truthy(schema: Any) -> bool:
        return schema is True or schema == {}

    def _canonicalish_checked(schema: Any) -> dict[str, Any]:
        result = _canonicalise.canonicalish(schema)
        _canonicalise._get_validator_class(result)
        return result

    def _merged(schemas: list[Any]) -> dict[str, Any] | None:
        if len(schemas) > 1:
            filtered = [schema for schema in schemas if not _is_trivial_truthy(schema)]
            if not filtered:
                return {}
            if len(filtered) == 1:
                return _canonicalish_checked(filtered[0])
            schemas = filtered

        if len(schemas) == 2:
            try:
                cache_key = (_schema_cache_key(schemas[0]), _schema_cache_key(schemas[1]))
            except (TypeError, ValueError):
                cache_key = None
            if cache_key is not None:
                cached = _merge_cache_get(cache_key)
                if cached is not False:
                    return cached
                reversed_key = (cache_key[1], cache_key[0])
                cached = _merge_cache_get(reversed_key)
                if cached is not False:
                    _merge_cache_set(cache_key, cached)
                    return cached

            result = _original_merged(schemas)
            if cache_key is not None:
                _merge_cache_set(cache_key, result)
            return result

        return _original_merged(schemas)

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
                result = _merged(
                    [_resolve_all_refs(s, resolver=resolver), _resolve_all_refs(deepclone(resolved), resolver=resolver)]
                )
            finally:
                resolver.pop_scope()
            if result is None:
                msg = f"$ref:{ref!r} had incompatible base schema {s!r}"
                raise _canonicalise.HypothesisRefResolutionError(msg)
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

    def _has_bundle_ref(obj: Any) -> bool:
        if isinstance(obj, dict):
            ref = obj.get("$ref")
            if isinstance(ref, str) and ref.startswith(REFERENCE_TO_BUNDLE_PREFIX):
                return True
            return any(_has_bundle_ref(v) for v in obj.values())
        if isinstance(obj, list):
            return any(_has_bundle_ref(item) for item in obj)
        return False

    def _fast_canonicalish(schema: Any) -> dict[str, Any]:
        if not isinstance(schema, dict) or BUNDLE_STORAGE_KEY not in schema:
            return _original_canonicalish(schema)
        bundle = schema[BUNDLE_STORAGE_KEY]
        schema_without_bundle = {k: v for k, v in schema.items() if k != BUNDLE_STORAGE_KEY}
        schema_for_canonicalish = schema if _has_bundle_ref(schema_without_bundle) else schema_without_bundle
        result = _original_canonicalish(schema_for_canonicalish)
        # Restore x-bundled so downstream $ref resolution can find bundled schemas.
        if isinstance(result, dict) and result and result != {"not": {}}:
            result[BUNDLE_STORAGE_KEY] = bundle
        return result

    _canonicalise.canonicalish = _fast_canonicalish
    _from_schema.canonicalish = _fast_canonicalish
    _resolve.canonicalish = _fast_canonicalish
    _canonicalise.merged = _merged
    _from_schema.merged = _merged
    root_core.BUFFER_SIZE = INTERNAL_BUFFER_SIZE
    engine.BUFFER_SIZE = INTERNAL_BUFFER_SIZE
    collections.BUFFER_SIZE = INTERNAL_BUFFER_SIZE

    # Patch make_validator to use jsonschema-rs for instance validation
    from schemathesis.core.errors import is_regex_validation_error
    from schemathesis.transport.serialization import contains_binary

    _original_get_validator_class = _canonicalise._get_validator_class

    class _ValidatorWrapper:
        __slots__ = ("_validator",)

        def __init__(self, validator: Any) -> None:
            self._validator = validator

        def is_valid(self, value: Any) -> bool:
            if contains_binary(value):
                return True
            return self._validator.is_valid(value)

    # `jsonschema_rs.validator_for` defaults to Draft 2020-12 when no `$schema` is present,
    # but hypothesis-jsonschema defaults to Draft 7. Schemas using Draft 4/7 features
    # (e.g. tuple `items`) are rejected by 2020-12, so we fall back through older drafts.
    # TODO: remove once hypothesis-jsonschema propagates the draft version consistently.
    def _make_rust_validator(schema: dict[str, Any]) -> Any:
        last_error: jsonschema_rs.ValidationError | None = None
        try:
            return jsonschema_rs.validator_for(schema, pattern_options=FANCY_REGEX_OPTIONS)
        except jsonschema_rs.ValidationError as exc:
            last_error = exc
            if is_regex_validation_error(exc):
                raise

        for cls in (jsonschema_rs.Draft7Validator, jsonschema_rs.Draft4Validator):
            try:
                return cls(schema, pattern_options=FANCY_REGEX_OPTIONS)
            except jsonschema_rs.ValidationError as exc:
                last_error = exc
                if is_regex_validation_error(exc):
                    raise

        assert last_error is not None
        raise last_error

    def make_validator(schema: dict[str, Any]) -> _ValidatorWrapper:
        try:
            validator = _make_rust_validator(schema)
            return _ValidatorWrapper(validator)
        except jsonschema_rs.ValidationError:
            # Either no Rust draft can compile this schema, or the regex engine differs.
            # In both cases fall back to the original Hypothesis validator.
            cls = _original_get_validator_class(schema)
            return _ValidatorWrapper(cls(schema))

    def _get_validator_class(schema: dict[str, Any]) -> Any:
        classes_to_try = [
            jsonschema_rs.validator_cls_for(schema),
            jsonschema_rs.Draft7Validator,
            jsonschema_rs.Draft4Validator,
        ]
        seen = set()
        last_error: jsonschema_rs.ValidationError | None = None

        for cls in classes_to_try:
            if cls in seen:
                continue
            seen.add(cls)
            try:
                cls(schema, pattern_options=FANCY_REGEX_OPTIONS)
                return cls
            except jsonschema_rs.ValidationError as exc:
                last_error = exc
                if is_regex_validation_error(exc):
                    # Keep the class selection from jsonschema-rs even when regex syntax differs.
                    return cls
                continue

        assert last_error is not None
        if last_error.kind.name == "$ref":
            # Unresolvable $ref — happens for intermediate sub-schemas that contain bundled
            # internal refs but have lost the bundle storage key during hypothesis-jsonschema's
            # merging. Fall back to Python; merged() discards the class anyway.
            return _original_get_validator_class(schema)
        raise last_error

    _canonicalise.make_validator = make_validator
    _from_schema.make_validator = make_validator
    _canonicalise._get_validator_class = _get_validator_class
    setup._is_patched = True  # type: ignore[attr-defined]
