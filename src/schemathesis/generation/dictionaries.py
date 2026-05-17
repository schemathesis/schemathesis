from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import jsonschema_rs
from hypothesis import strategies as st

from schemathesis.config._dictionaries import (
    SUPPORTED_TYPE_KEYS,
    DictionaryBinding,
    DictionaryDefinition,
    EntryValue,
    ParameterDictionaryBinding,
    coerce_entries_for_type,
    lookup_parameter,
)
from schemathesis.core.jsonschema import make_validator
from schemathesis.core.jsonschema.types import JsonSchema, JsonValue, get_type
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.resources import PoolDraw, SemanticDraw

if TYPE_CHECKING:
    from schemathesis.config import GenerationConfig, ProjectConfig
    from schemathesis.specs.openapi.negative import GeneratedValue
    from schemathesis.specs.openapi.negative.mutations import MutationMetadata
    from schemathesis.specs.openapi.schemas import OpenApiOperation


def _eligible_for_mode(matches_schema: bool, mode: GenerationMode) -> bool:
    if mode is GenerationMode.POSITIVE:
        return matches_schema
    return not matches_schema


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
    from schemathesis.specs.openapi.negative import GeneratedValue

    location_value = parameter_location.value

    eligible_per_parameter: dict[str, tuple[tuple[int, EntryValue, bool], ...]] = {}
    for parameter_name, binding in bindings.items():
        schema = schema_properties.get(parameter_name)
        validator: jsonschema_rs.Validator | None = None
        if isinstance(schema, dict):
            try:
                validator = make_validator(schema, validator_cls)
            except jsonschema_rs.ValidationError:
                # Malformed property schema (e.g. invalid regex); skip validity classification.
                validator = None
        classified: list[tuple[int, EntryValue, bool]] = []
        for entry_index, entry_value in binding.entries:
            # Unknown validity: route only into NEGATIVE so unverified entries can't pollute positive cases.
            matches_schema = False if validator is None else validator.is_valid(entry_value)
            if _eligible_for_mode(matches_schema, generation_mode):
                classified.append((entry_index, entry_value, matches_schema))
        if classified:
            eligible_per_parameter[parameter_name] = tuple(classified)

    if not eligible_per_parameter:
        return inner

    @st.composite  # type: ignore[untyped-decorator]
    def overlay(draw: st.DrawFn) -> GeneratedValue | dict[str, JsonValue]:
        produced = draw(inner)
        existing_meta: MutationMetadata | None = None
        existing_pool: tuple[PoolDraw, ...] = ()
        existing_semantic: tuple[SemanticDraw, ...] = ()
        existing_dictionary: tuple[DictionaryDraw, ...] = ()
        if isinstance(produced, GeneratedValue):
            value = produced.value
            existing_meta = produced.meta
            existing_pool = produced.pool_draws
            existing_semantic = produced.semantic_draws
            existing_dictionary = produced.dictionary_draws
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
        )

    return overlay()
