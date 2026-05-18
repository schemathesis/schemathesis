from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from hypothesis import strategies as st

from schemathesis.config._dictionaries import BODY_PREFIX, ParameterDictionaryBinding, parse_body_path
from schemathesis.core.jsonschema.types import JsonSchema, JsonValue
from schemathesis.generation.dictionaries import _path_matches_pattern, _resolve_body_leaf_schema, _walk_substitute
from schemathesis.generation.value import GeneratedValue

if TYPE_CHECKING:
    from schemathesis.specs.openapi.schemas import OpenApiOperation


def resolve_body_overrides(*, operation: OpenApiOperation, body_schema: JsonSchema) -> dict[str, JsonValue]:
    # Operation-scope vetoes global entries under the same key regardless of form; unresolved paths drop.
    config = operation.schema.config
    operation_config = config.operations.get_for_operation(operation)
    operation_scoped_body_keys = {key for key in operation_config.parameters if key.startswith(BODY_PREFIX)}
    resolved: dict[str, JsonValue] = {}
    for is_operation_source, source in ((True, operation_config.parameters), (False, config.parameters)):
        for key, value in source.items():
            if not key.startswith(BODY_PREFIX):
                continue
            if not is_operation_source and key in operation_scoped_body_keys:
                continue
            if isinstance(value, ParameterDictionaryBinding):
                continue
            pointer = parse_body_path(key)
            if _resolve_body_leaf_schema(body_schema, pointer) is None:
                continue
            resolved.setdefault(pointer, value)
    return resolved


def build_body_override_overlay_strategy(
    inner: st.SearchStrategy,
    *,
    overrides: dict[str, JsonValue],
) -> st.SearchStrategy:
    items = tuple(overrides.items())
    override_segments = [pointer[1:].split("/") for pointer in overrides]

    def apply(value: JsonValue) -> JsonValue:
        for pointer, literal in items:
            value = _walk_substitute(value, pointer[1:].split("/"), _make_replace(literal), create_missing=True)
        return value

    def is_overridden_path(path: tuple[str | int, ...]) -> bool:
        return any(_path_matches_pattern(path, segments) for segments in override_segments)

    @st.composite  # type: ignore[untyped-decorator]
    def overlay(draw: st.DrawFn) -> GeneratedValue | JsonValue:
        produced = draw(inner)
        if isinstance(produced, GeneratedValue):
            meta = produced.meta
            if meta is not None:
                kept = tuple(m for m in meta.mutations if not is_overridden_path(m.path))
                if not kept:
                    meta = None
                elif len(kept) != len(meta.mutations):
                    meta = type(meta)(mutations=kept)
            dictionary_draws = tuple(
                d
                for d in produced.dictionary_draws
                if d.body_path is None or not is_overridden_path(tuple(d.body_path[1:].split("/")))
            )
            return GeneratedValue(
                value=apply(produced.value),
                meta=meta,
                pool_draws=produced.pool_draws,
                semantic_draws=produced.semantic_draws,
                dictionary_draws=dictionary_draws,
            )
        return apply(produced)

    return overlay()


def _make_replace(literal: JsonValue) -> Callable[[JsonValue], JsonValue]:
    def replace(_current: JsonValue) -> JsonValue:
        return literal

    return replace
