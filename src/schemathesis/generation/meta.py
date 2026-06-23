from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from schemathesis.core.mutations import Mutation
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.resources import PoolDraw, SemanticDraw

if TYPE_CHECKING:
    from schemathesis.generation.dictionaries import DictionaryDraw


class TestPhase(str, Enum):
    __test__ = False

    EXAMPLES = "examples"
    COVERAGE = "coverage"
    FUZZING = "fuzzing"
    STATEFUL = "stateful"


class CoverageScenario(str, Enum):
    """Coverage test scenario types."""

    # Positive scenarios - Valid values
    EXAMPLE_VALUE = "example_value"
    DEFAULT_VALUE = "default_value"
    ENUM_VALUE = "enum_value"
    CONST_VALUE = "const_value"
    VALID_STRING = "valid_string"
    VALID_NUMBER = "valid_number"
    VALID_BOOLEAN = "valid_boolean"
    VALID_ARRAY = "valid_array"
    VALID_OBJECT = "valid_object"
    NULL_VALUE = "null_value"

    # Positive scenarios - Boundary values for strings
    MINIMUM_LENGTH_STRING = "minimum_length_string"
    MAXIMUM_LENGTH_STRING = "maximum_length_string"
    NEAR_BOUNDARY_LENGTH_STRING = "near_boundary_length_string"

    # Positive scenarios - Boundary values for numbers
    MINIMUM_VALUE = "minimum_value"
    MAXIMUM_VALUE = "maximum_value"
    NEAR_BOUNDARY_NUMBER = "near_boundary_number"

    # Positive scenarios - Boundary values for arrays
    MINIMUM_ITEMS_ARRAY = "minimum_items_array"
    MAXIMUM_ITEMS_ARRAY = "maximum_items_array"
    NEAR_BOUNDARY_ITEMS_ARRAY = "near_boundary_items_array"
    ENUM_VALUE_ITEMS_ARRAY = "enum_value_items_array"

    # Positive scenarios - Objects
    OBJECT_ONLY_REQUIRED = "object_only_required"
    OBJECT_REQUIRED_AND_OPTIONAL = "object_required_and_optional"
    OBJECT_ADDITIONAL_PROPERTY = "object_additional_property"

    # Positive scenarios - Default test case
    DEFAULT_POSITIVE_TEST = "default_positive_test"

    # Negative scenarios - Boundary violations for numbers
    VALUE_ABOVE_MAXIMUM = "value_above_maximum"
    VALUE_BELOW_MINIMUM = "value_below_minimum"

    # Negative scenarios - Boundary violations for strings
    STRING_ABOVE_MAX_LENGTH = "string_above_max_length"
    STRING_BELOW_MIN_LENGTH = "string_below_min_length"

    # Negative scenarios - Boundary violations for arrays
    ARRAY_ABOVE_MAX_ITEMS = "array_above_max_items"
    ARRAY_BELOW_MIN_ITEMS = "array_below_min_items"

    # Negative scenarios - Boundary violations for objects
    OBJECT_ABOVE_MAX_PROPERTIES = "object_above_max_properties"
    OBJECT_BELOW_MIN_PROPERTIES = "object_below_min_properties"

    # Negative scenarios - Constraint violations
    OBJECT_UNEXPECTED_PROPERTIES = "object_unexpected_properties"
    OBJECT_MISSING_REQUIRED_PROPERTY = "object_missing_required_property"
    OBJECT_INVALID_PROPERTY_NAME = "object_invalid_property_name"
    INCORRECT_TYPE = "incorrect_type"
    INVALID_ENUM_VALUE = "invalid_enum_value"
    INVALID_FORMAT = "invalid_format"
    INVALID_PATTERN = "invalid_pattern"
    NOT_MULTIPLE_OF = "not_multiple_of"
    NON_UNIQUE_ITEMS = "non_unique_items"
    UNIQUE_ITEMS_ARRAY = "unique_items_array"

    # Negative scenarios - Missing parameters
    MISSING_PARAMETER = "missing_parameter"
    DUPLICATE_PARAMETER = "duplicate_parameter"

    # Negative scenarios - Unsupported patterns
    UNSUPPORTED_PATH_PATTERN = "unsupported_path_pattern"
    UNSPECIFIED_HTTP_METHOD = "unspecified_http_method"


@dataclass
class ComponentInfo:
    """Information about how a specific component was generated."""

    mode: GenerationMode

    __slots__ = ("mode",)


@dataclass(slots=True)
class FuzzingPhaseData:
    """Metadata specific to fuzzing phase."""

    description: str | None
    parameter: str | None
    parameter_location: ParameterLocation | None
    location: str | None
    mutations: tuple[Mutation, ...] = ()


@dataclass(slots=True)
class StatefulPhaseData:
    """Metadata specific to stateful phase."""

    description: str | None
    parameter: str | None
    parameter_location: ParameterLocation | None
    location: str | None
    mutations: tuple[Mutation, ...] = ()


@dataclass(slots=True)
class ExamplesPhaseData:
    """Metadata specific to examples phase."""

    description: str | None
    parameter: str | None
    parameter_location: ParameterLocation | None
    location: str | None
    mutations: tuple[Mutation, ...] = ()


@dataclass(slots=True)
class CoveragePhaseData:
    """Metadata specific to coverage phase."""

    scenario: CoverageScenario
    description: str
    location: str | None
    parameter: str | None
    parameter_location: ParameterLocation | None
    mutations: tuple[Mutation, ...] = ()


@dataclass
class PhaseInfo:
    """Phase-specific information."""

    name: TestPhase
    data: CoveragePhaseData | ExamplesPhaseData | FuzzingPhaseData | StatefulPhaseData

    __slots__ = ("name", "data")

    @classmethod
    def coverage(
        cls,
        scenario: CoverageScenario,
        description: str,
        location: str | None = None,
        parameter: str | None = None,
        parameter_location: ParameterLocation | None = None,
    ) -> PhaseInfo:
        return cls(
            name=TestPhase.COVERAGE,
            data=CoveragePhaseData(
                scenario=scenario,
                description=description,
                location=location,
                parameter=parameter,
                parameter_location=parameter_location,
            ),
        )


@dataclass
class GenerationInfo:
    """Information about test case generation."""

    time: float
    mode: GenerationMode

    __slots__ = ("time", "mode")


@dataclass(slots=True)
class CaseMetadata:
    """Complete metadata for generated cases."""

    generation: GenerationInfo
    components: dict[ParameterLocation, ComponentInfo]
    phase: PhaseInfo
    pool_draws: tuple[PoolDraw, ...] = ()
    # Resource-bound (location, parameter_name) slots the engine wanted to fill from the pool
    # but couldn't (no captured instance). Lets the analyzer compute chain rate.
    pool_misses: tuple[tuple[str, str], ...] = ()
    # Semantic value index substitutions applied by the overlay (one entry per leaf substituted).
    semantic_draws: tuple[SemanticDraw, ...] = ()
    # Configured fuzz-dictionary draws applied to named parameters at generation time.
    dictionary_draws: tuple[DictionaryDraw, ...] = ()
    # Typed (pre-serialization) container snapshots captured at generation time.
    # Coverage stringifies query/path values for the wire; this preserves the
    # original typed form so revalidation matches the schema's abstraction level.
    raw_containers: dict[ParameterLocation, Any] = field(default_factory=dict)

    # Dirty tracking for revalidation
    _dirty: set[ParameterLocation] = field(default_factory=set, init=False)
    _last_validated_hashes: dict[ParameterLocation, int] = field(default_factory=dict, init=False)
    # Initial container hashes captured once after generation; never updated.
    # Lets revalidation detect whether a container still matches its wire-form
    # snapshot (so the typed `raw_containers` value remains authoritative).
    _initial_hashes: dict[ParameterLocation, int] = field(default_factory=dict, init=False)

    def mark_dirty(self, location: ParameterLocation) -> None:
        """Mark a component as modified and needing revalidation."""
        self._dirty.add(location)

    def clear_dirty(self, location: ParameterLocation) -> None:
        """Clear dirty flag for a component after revalidation."""
        self._dirty.discard(location)

    def is_dirty(self) -> bool:
        """Check if any component needs revalidation."""
        return len(self._dirty) > 0

    def update_validated_hash(self, location: ParameterLocation, value: int) -> None:
        """Store hash after validation to detect future changes."""
        self._last_validated_hashes[location] = value

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for cross-process transport."""
        phase_data = self.phase.data
        phase_data_dict: dict[str, Any] = {
            "type": type(phase_data).__name__,
            "description": phase_data.description,
            "location": phase_data.location,
            "parameter": phase_data.parameter,
            "parameter_location": (
                phase_data.parameter_location.name if phase_data.parameter_location is not None else None
            ),
        }
        if isinstance(phase_data, CoveragePhaseData):
            phase_data_dict["scenario"] = phase_data.scenario.value
        elif isinstance(phase_data, (FuzzingPhaseData, StatefulPhaseData, ExamplesPhaseData)):
            phase_data_dict["mutations"] = [m.to_dict() for m in phase_data.mutations]
        return {
            "generation": {
                "time": self.generation.time,
                "mode": self.generation.mode.value,
            },
            "components": {loc.name: info.mode.value for loc, info in self.components.items()},
            "phase": {
                "name": self.phase.name.value,
                "data": phase_data_dict,
            },
            "pool_draws": [
                {
                    "location": draw.location,
                    "parameter_name": draw.parameter_name,
                    "resource_name": draw.resource_name,
                    "resource_field": draw.resource_field,
                    "source_operation": draw.source_operation,
                    "source_status": draw.source_status,
                }
                for draw in self.pool_draws
            ],
            "pool_misses": [list(miss) for miss in self.pool_misses],
            "semantic_draws": [
                {
                    "path": list(draw.path),
                    "type_token": draw.type_token,
                    "format_token": draw.format_token,
                    "pattern_hash": draw.pattern_hash,
                    "normalized_name": draw.normalized_name,
                    "value": draw.value,
                    "source_operation": draw.source_operation,
                }
                for draw in self.semantic_draws
            ],
            "dictionary_draws": [
                {
                    "dictionary": draw.dictionary,
                    "source_kind": draw.source_kind,
                    "source_path": draw.source_path,
                    "entry_index": draw.entry_index,
                    "operation_label": draw.operation_label,
                    "parameter_location": draw.parameter_location,
                    "parameter_name": draw.parameter_name,
                    "value": draw.value,
                    "matches_schema": draw.matches_schema,
                }
                for draw in self.dictionary_draws
            ],
            "raw_containers": {loc.name: value for loc, value in self.raw_containers.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> CaseMetadata:
        """Reconstruct from a plain dict produced by ``to_dict``."""
        generation = GenerationInfo(
            time=data["generation"]["time"],
            mode=GenerationMode(data["generation"]["mode"]),
        )
        components = {
            ParameterLocation[loc_name]: ComponentInfo(mode=GenerationMode(mode_val))
            for loc_name, mode_val in data["components"].items()
        }
        pool_draws = tuple(
            PoolDraw(
                location=draw["location"],
                parameter_name=draw["parameter_name"],
                resource_name=draw["resource_name"],
                resource_field=draw["resource_field"],
                source_operation=draw["source_operation"],
                source_status=draw["source_status"],
            )
            for draw in data.get("pool_draws", [])
        )
        pool_misses = tuple((miss[0], miss[1]) for miss in data.get("pool_misses", []))
        semantic_draws = tuple(
            SemanticDraw(
                path=tuple(draw["path"]),
                type_token=draw["type_token"],
                format_token=draw["format_token"],
                pattern_hash=draw["pattern_hash"],
                normalized_name=draw["normalized_name"],
                value=draw["value"],
                source_operation=draw["source_operation"],
            )
            for draw in data.get("semantic_draws", [])
        )
        phase_data_raw = data["phase"]["data"]
        param_loc_name = phase_data_raw["parameter_location"]
        param_loc = ParameterLocation[param_loc_name] if param_loc_name is not None else None
        phase_data_type = phase_data_raw["type"]
        phase_data: CoveragePhaseData | FuzzingPhaseData | StatefulPhaseData | ExamplesPhaseData
        if phase_data_type == "CoveragePhaseData":
            phase_data = CoveragePhaseData(
                scenario=CoverageScenario(phase_data_raw["scenario"]),
                description=phase_data_raw["description"],
                location=phase_data_raw["location"],
                parameter=phase_data_raw["parameter"],
                parameter_location=param_loc,
            )
        elif phase_data_type == "FuzzingPhaseData":
            phase_data = FuzzingPhaseData(
                description=phase_data_raw["description"],
                parameter=phase_data_raw["parameter"],
                parameter_location=param_loc,
                location=phase_data_raw["location"],
                mutations=_load_mutations(phase_data_raw),
            )
        elif phase_data_type == "StatefulPhaseData":
            phase_data = StatefulPhaseData(
                description=phase_data_raw["description"],
                parameter=phase_data_raw["parameter"],
                parameter_location=param_loc,
                location=phase_data_raw["location"],
                mutations=_load_mutations(phase_data_raw),
            )
        else:
            phase_data = ExamplesPhaseData(
                description=phase_data_raw["description"],
                parameter=phase_data_raw["parameter"],
                parameter_location=param_loc,
                location=phase_data_raw["location"],
                mutations=_load_mutations(phase_data_raw),
            )
        phase = PhaseInfo(name=TestPhase(data["phase"]["name"]), data=phase_data)
        raw_containers = {
            ParameterLocation[loc_name]: value for loc_name, value in data.get("raw_containers", {}).items()
        }
        from schemathesis.generation.dictionaries import DictionaryDraw

        dictionary_draws = tuple(
            DictionaryDraw(
                dictionary=draw["dictionary"],
                source_kind=draw["source_kind"],
                source_path=draw["source_path"],
                entry_index=draw["entry_index"],
                operation_label=draw["operation_label"],
                parameter_location=draw["parameter_location"],
                parameter_name=draw["parameter_name"],
                value=draw["value"],
                matches_schema=draw["matches_schema"],
            )
            for draw in data.get("dictionary_draws", [])
        )
        return cls(
            generation=generation,
            components=components,
            phase=phase,
            pool_draws=pool_draws,
            pool_misses=pool_misses,
            semantic_draws=semantic_draws,
            dictionary_draws=dictionary_draws,
            raw_containers=raw_containers,
        )


def _load_mutations(raw: dict[str, Any]) -> tuple[Mutation, ...]:
    return tuple(Mutation.from_dict(m) for m in raw.get("mutations", ()))
