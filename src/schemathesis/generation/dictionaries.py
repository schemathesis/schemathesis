from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import jsonschema_rs
from hypothesis import strategies as st

from schemathesis.config._dictionaries import (
    BODY_PREFIX,
    SUPPORTED_TYPE_KEYS,
    DictionaryBinding,
    DictionaryDefinition,
    EntryValue,
    ParameterDictionaryBinding,
    coerce_entries_for_type,
    lookup_parameter,
    parse_body_path,
)
from schemathesis.core.jsonschema import make_validator
from schemathesis.core.jsonschema.types import JsonSchema, JsonValue, get_type
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.generation.value import GeneratedValue
from schemathesis.resources import PoolDraw, SemanticDraw
from schemathesis.specs.openapi.adapter.parameters import _prune_overwritten_constants

if TYPE_CHECKING:
    from schemathesis.config import GenerationConfig, ProjectConfig
    from schemathesis.python._constants.pool import ConstantDraw
    from schemathesis.specs.openapi.negative.mutations import MutationMetadata
    from schemathesis.specs.openapi.schemas import OpenApiOperation


def _eligible_for_mode(matches_schema: bool, mode: GenerationMode) -> bool:
    if mode is GenerationMode.POSITIVE:
        return matches_schema
    return not matches_schema


def _classify_entries(
    entries: tuple[tuple[int, EntryValue], ...],
    schema: JsonSchema | None,
    validator_cls: type,
    generation_mode: GenerationMode,
) -> tuple[tuple[int, EntryValue, bool], ...]:
    # `None` / non-dict schemas route only into NEGATIVE so unverified entries can't pollute positive cases.
    accepts_anything = schema is True
    validator: jsonschema_rs.Validator | None = None
    if not accepts_anything and isinstance(schema, dict):
        validator = make_validator(schema, validator_cls)
    classified: list[tuple[int, EntryValue, bool]] = []
    for entry_index, entry_value in entries:
        if accepts_anything:
            matches_schema = True
        elif validator is None:
            matches_schema = False
        else:
            matches_schema = validator.is_valid(entry_value)
        if _eligible_for_mode(matches_schema, generation_mode):
            classified.append((entry_index, entry_value, matches_schema))
    return tuple(classified)


@dataclass(slots=True, frozen=True)
class DictionaryDraw:
    dictionary: str
    source_kind: str
    source_path: str | None
    entry_index: int
    operation_label: str
    parameter_location: str
    parameter_name: str
    value: EntryValue
    matches_schema: bool
    body_path: str | None = None


@dataclass(slots=True, frozen=True)
class _ResolvedBinding:
    parameter_name: str
    dictionary: DictionaryDefinition
    probability: float
    entries: tuple[tuple[int, EntryValue], ...]
    # True when this binding came from `[parameters]` or `[[operations]] parameters`: the user
    # targeted a specific parameter, so a missing optional gets force-inserted on substitution.
    is_parameter_specific: bool


def resolve_parameter_bindings(
    *,
    operation: OpenApiOperation,
    location: ParameterLocation,
    properties: dict[str, JsonSchema],
    generation_config: GenerationConfig,
) -> dict[str, _ResolvedBinding]:
    # `properties` must be the overlay's validator view so bindings can't outlive removed params.
    # Precedence: op-specific parameter > global parameter > op-specific type-wide > global type-wide.
    config: ProjectConfig = operation.schema.config
    dictionaries = config.dictionaries
    if not dictionaries:
        return {}

    operation_config = config.operations.get_for_operation(operation)
    location_value = location.value

    type_wide_bindings = generation_config.dictionaries

    resolved: dict[str, _ResolvedBinding] = {}
    for parameter_name, parameter_schema in properties.items():
        binding: ParameterDictionaryBinding | DictionaryBinding | None = _find_parameter_binding(
            operation_config.parameters, parameter_name=parameter_name, location=location_value
        )
        if binding is None:
            binding = _find_parameter_binding(config.parameters, parameter_name=parameter_name, location=location_value)
        ty: str | None = None
        if binding is None and type_wide_bindings:
            ty = _infer_ty(parameter_schema)
            if ty is not None:
                binding = type_wide_bindings.get(ty)
        if binding is None:
            continue

        dictionary = dictionaries[binding.dictionary]
        is_parameter_specific = isinstance(binding, ParameterDictionaryBinding)
        if is_parameter_specific:
            entries = tuple((entry.index, entry.value) for entry in dictionary.entries)
        else:
            assert ty is not None
            entries = coerce_entries_for_type(dictionary.entries, ty)

        resolved[parameter_name] = _ResolvedBinding(
            parameter_name=parameter_name,
            dictionary=dictionary,
            probability=binding.probability,
            entries=entries,
            is_parameter_specific=is_parameter_specific,
        )

    return resolved


def _find_parameter_binding(
    parameters: dict[str, object], *, parameter_name: str, location: str
) -> ParameterDictionaryBinding | None:
    value = lookup_parameter(parameters, name=parameter_name, location=location)
    if isinstance(value, ParameterDictionaryBinding):
        return value
    return None


def _infer_ty(parameter_schema: JsonSchema) -> str | None:
    if not isinstance(parameter_schema, dict) or "type" not in parameter_schema:
        return None
    return next((ty for ty in get_type(parameter_schema) if ty in SUPPORTED_TYPE_KEYS), None)


def build_dictionary_overlay_strategy(
    inner: st.SearchStrategy,
    *,
    bindings: dict[str, _ResolvedBinding],
    operation_label: str,
    parameter_location: ParameterLocation,
    schema_properties: dict[str, JsonSchema],
    validator_cls: type,
    generation_mode: GenerationMode,
) -> st.SearchStrategy:
    # Build-time mode filter so a substitution can't silently flip the case mode chosen by `inner`.
    location_value = parameter_location.value

    eligible_per_parameter: dict[str, tuple[tuple[int, EntryValue, bool], ...]] = {}
    for parameter_name, binding in bindings.items():
        classified = _classify_entries(
            binding.entries,
            schema_properties.get(parameter_name),
            validator_cls,
            generation_mode,
        )
        if classified:
            eligible_per_parameter[parameter_name] = classified

    if not eligible_per_parameter:
        return inner

    @st.composite  # type: ignore[untyped-decorator]
    def overlay(draw: st.DrawFn) -> GeneratedValue | dict[str, JsonValue]:
        produced = draw(inner)
        existing_meta: MutationMetadata | None = None
        existing_pool: tuple[PoolDraw, ...] = ()
        existing_semantic: tuple[SemanticDraw, ...] = ()
        existing_dictionary: tuple[DictionaryDraw, ...] = ()
        existing_constants: tuple[ConstantDraw, ...] = ()
        if isinstance(produced, GeneratedValue):
            value = produced.value
            existing_meta = produced.meta
            existing_pool = produced.pool_draws
            existing_semantic = produced.semantic_draws
            existing_dictionary = produced.dictionary_draws
            existing_constants = produced.constants_draws
        else:
            value = produced

        random = draw(st.randoms())

        # Share the slot 50/50 when the semantic-pool overlay already filled the same parameter.
        semantically_substituted: set[str] = {
            semantic.path[0] for semantic in existing_semantic if len(semantic.path) == 1
        }

        new_value = dict(value)
        new_draws: list[DictionaryDraw] = []
        for parameter_name, eligible in eligible_per_parameter.items():
            binding = bindings[parameter_name]
            present = parameter_name in new_value
            # Type-wide fills only if the strategy already included the param; parameter-specific forces it.
            if not present and not binding.is_parameter_specific:
                continue
            if parameter_name in semantically_substituted:
                if random.random() >= 0.5:
                    continue
            elif random.random() >= binding.probability:
                continue
            entry_index, entry_value, matches_schema = draw(st.sampled_from(eligible))
            new_value[parameter_name] = entry_value
            new_draws.append(
                DictionaryDraw(
                    dictionary=binding.dictionary.name,
                    source_kind=binding.dictionary.source_kind,
                    source_path=binding.dictionary.source_path,
                    entry_index=entry_index,
                    operation_label=operation_label,
                    parameter_location=location_value,
                    parameter_name=parameter_name,
                    value=entry_value,
                    matches_schema=matches_schema,
                )
            )

        if not new_draws and not isinstance(produced, GeneratedValue):
            return produced

        # Drop mutations for parameters the dictionary overwrote so attribution matches the wire.
        meta_to_return = existing_meta
        if existing_meta is not None and new_draws:
            overwritten = {draw_.parameter_name for draw_ in new_draws}
            kept = tuple(
                mutation
                for mutation in existing_meta.mutations
                if mutation.parameter is None or mutation.parameter not in overwritten
            )
            if not kept:
                meta_to_return = None
            elif len(kept) != len(existing_meta.mutations):
                meta_to_return = type(existing_meta)(mutations=kept)

        combined_draws = existing_dictionary + tuple(new_draws)
        return GeneratedValue(
            value=new_value,
            meta=meta_to_return,
            pool_draws=existing_pool,
            semantic_draws=existing_semantic,
            dictionary_draws=combined_draws,
            constants_draws=_prune_overwritten_constants(existing_constants, new_value),
        )

    return overlay()


@dataclass(slots=True, frozen=True)
class _ResolvedBodyBinding:
    pointer: str
    dictionary: DictionaryDefinition
    probability: float
    entries: tuple[tuple[int, EntryValue], ...]
    leaf_schema: JsonSchema


def resolve_body_bindings(
    *,
    operation: OpenApiOperation,
    body_schema: JsonSchema,
    generation_config: GenerationConfig,
) -> list[_ResolvedBodyBinding]:
    # Operation-scope vetoes global entries under the same key regardless of form; unresolved paths drop.
    config: ProjectConfig = operation.schema.config
    dictionaries = config.dictionaries
    if not dictionaries:
        return []
    operation_config = config.operations.get_for_operation(operation)
    operation_scoped_body_keys = {key for key in operation_config.parameters if key.startswith(BODY_PREFIX)}
    seen: set[str] = set()
    resolved: list[_ResolvedBodyBinding] = []
    for is_operation_source, source in ((True, operation_config.parameters), (False, config.parameters)):
        for key, binding in source.items():
            if not key.startswith(BODY_PREFIX):
                continue
            if not is_operation_source and key in operation_scoped_body_keys:
                continue
            if not isinstance(binding, ParameterDictionaryBinding):
                continue
            if key in seen:
                continue
            seen.add(key)
            pointer = parse_body_path(key)
            leaf_schema = _resolve_body_leaf_schema(body_schema, pointer)
            if leaf_schema is None:
                continue
            dictionary = dictionaries[binding.dictionary]
            entries = tuple((entry.index, entry.value) for entry in dictionary.entries)
            resolved.append(
                _ResolvedBodyBinding(
                    pointer=pointer,
                    dictionary=dictionary,
                    probability=binding.probability,
                    entries=entries,
                    leaf_schema=leaf_schema,
                )
            )
    return resolved


def _resolve_body_leaf_schema(schema: JsonSchema, pointer: str) -> JsonSchema | None:
    # `pointer` always comes from `parse_body_path`, so it is `/`-prefixed and non-empty.
    current: JsonSchema = schema
    for segment in pointer[1:].split("/"):
        if not isinstance(current, dict):
            return None
        if segment == "*":
            items = current.get("items")
            if not isinstance(items, (dict, bool)):
                return None
            current = items
        else:
            properties = current.get("properties")
            if not isinstance(properties, dict) or segment not in properties:
                return None
            current = properties[segment]
    return current if isinstance(current, (dict, bool)) else None


def build_body_dictionary_overlay_strategy(
    inner: st.SearchStrategy,
    *,
    bindings: list[_ResolvedBodyBinding],
    operation_label: str,
    validator_cls: type,
    generation_mode: GenerationMode,
) -> st.SearchStrategy:
    # Build-time mode filter so a substitution can't silently flip the case mode chosen by `inner`.
    eligible_per_binding: list[tuple[_ResolvedBodyBinding, tuple[tuple[int, EntryValue, bool], ...]]] = []
    for binding in bindings:
        classified = _classify_entries(binding.entries, binding.leaf_schema, validator_cls, generation_mode)
        if classified:
            eligible_per_binding.append((binding, classified))

    if not eligible_per_binding:
        return inner

    @st.composite  # type: ignore[untyped-decorator]
    def overlay(draw: st.DrawFn) -> GeneratedValue | JsonValue:
        produced = draw(inner)
        existing_meta: MutationMetadata | None = None
        existing_pool: tuple[PoolDraw, ...] = ()
        existing_semantic: tuple[SemanticDraw, ...] = ()
        existing_dictionary: tuple[DictionaryDraw, ...] = ()
        existing_constants: tuple[ConstantDraw, ...] = ()
        if isinstance(produced, GeneratedValue):
            value = produced.value
            existing_meta = produced.meta
            existing_pool = produced.pool_draws
            existing_semantic = produced.semantic_draws
            existing_dictionary = produced.dictionary_draws
            existing_constants = produced.constants_draws
        else:
            value = produced

        random = draw(st.randoms())
        new_draws: list[DictionaryDraw] = []
        for binding, eligible in eligible_per_binding:
            value = _walk_substitute(
                value,
                binding.pointer[1:].split("/"),
                _make_replacement(
                    draw=draw,
                    random=random,
                    binding=binding,
                    eligible=eligible,
                    operation_label=operation_label,
                    new_draws=new_draws,
                ),
            )

        if not new_draws and not isinstance(produced, GeneratedValue):
            return produced

        meta_to_return = existing_meta
        if existing_meta is not None and new_draws:
            overwritten_segments = [draw_.body_path[1:].split("/") for draw_ in new_draws if draw_.body_path]
            kept = tuple(
                mutation
                for mutation in existing_meta.mutations
                if not any(_path_matches_pattern(mutation.path, pattern) for pattern in overwritten_segments)
            )
            if not kept:
                meta_to_return = None
            elif len(kept) != len(existing_meta.mutations):
                meta_to_return = type(existing_meta)(mutations=kept)

        combined_draws = existing_dictionary + tuple(new_draws)
        return GeneratedValue(
            value=value,
            meta=meta_to_return,
            pool_draws=existing_pool,
            semantic_draws=existing_semantic,
            dictionary_draws=combined_draws,
            constants_draws=_prune_overwritten_constants(existing_constants, value),
        )

    return overlay()


def _path_matches_pattern(path: tuple[str | int, ...], segments: list[str]) -> bool:
    # Match the overwritten leaf or any descendant: dropping `/user` covers `(user, email)`.
    if len(path) < len(segments):
        return False
    for part, segment in zip(path, segments, strict=False):
        if segment == "*":
            if not isinstance(part, int):
                return False
        elif part != segment:
            return False
    return True


def _make_replacement(
    *,
    draw: st.DrawFn,
    random: random.Random,
    binding: _ResolvedBodyBinding,
    eligible: tuple[tuple[int, EntryValue, bool], ...],
    operation_label: str,
    new_draws: list[DictionaryDraw],
) -> Callable[[JsonValue], JsonValue]:
    def replace(current: JsonValue) -> JsonValue:
        if random.random() >= binding.probability:
            return current
        entry_index, entry_value, matches_schema = draw(st.sampled_from(eligible))
        new_draws.append(
            DictionaryDraw(
                dictionary=binding.dictionary.name,
                source_kind=binding.dictionary.source_kind,
                source_path=binding.dictionary.source_path,
                entry_index=entry_index,
                operation_label=operation_label,
                parameter_location="body",
                parameter_name=binding.pointer.rsplit("/", 1)[-1] or "",
                value=entry_value,
                matches_schema=matches_schema,
                body_path=binding.pointer,
            )
        )
        return entry_value

    return replace


def _walk_substitute(
    target: JsonValue,
    segments: list[str],
    replace: Callable[[JsonValue], JsonValue],
    *,
    create_missing: bool = False,
) -> JsonValue:
    if not segments:
        return replace(target)
    head, *rest = segments
    if head == "*":
        if not isinstance(target, list):
            return target
        return [_walk_substitute(item, rest, replace, create_missing=create_missing) for item in target]
    if isinstance(target, dict):
        if head not in target:
            # Force-insert only when the remaining path is fully scalar — a `*` in `rest` would
            # need an array of unknown length, so leave missing optional arrays untouched.
            if not create_missing or "*" in rest:
                return target
            new_dict = dict(target)
            new_dict[head] = _walk_substitute({}, rest, replace, create_missing=True)
            return new_dict
        new_dict = dict(target)
        new_dict[head] = _walk_substitute(target[head], rest, replace, create_missing=create_missing)
        return new_dict
    if create_missing and "*" not in segments and isinstance(target, (str, int, float, bool, type(None))):
        return _walk_substitute({}, segments, replace, create_missing=True)
    return target
