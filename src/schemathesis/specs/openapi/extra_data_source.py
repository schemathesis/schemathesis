from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

from schemathesis.core import NOT_SET, deserialization
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.core.parameters import ParameterLocation
from schemathesis.resources import ExtraDataSource
from schemathesis.resources.repository import ResourceRepository
from schemathesis.specs.openapi.stateful.dependencies.models import DependencyGraph

if TYPE_CHECKING:
    from random import Random

    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

RequirementKey = tuple[str, ParameterLocation, str]
DedupKey: TypeAlias = tuple[type, str | int | float | bool | None]

# Decay factor for recency weighting. Higher = faster recovery of weight.
RECENCY_DECAY_FACTOR = 3.0
# Maximum number of variants to track. Oldest entries are evicted when exceeded.
MAX_TRACKED_VARIANTS = 10000


class VariantUsageTracker:
    """Tracks variant usage for weighted sampling.

    Maintains a global step counter and records when each variant was last drawn.
    Recently drawn variants get lower weights to encourage diversity.
    Uses LRU eviction to bound memory usage.
    """

    __slots__ = ("_step", "_last_drawn", "_maxlen", "_lock")

    def __init__(self, maxlen: int = MAX_TRACKED_VARIANTS) -> None:
        self._step = 0
        self._last_drawn: dict[str, int] = {}
        self._maxlen = maxlen
        self._lock = threading.Lock()

    def weighted_select(self, variant_keys: list[str], random: Random) -> int:
        """Select a variant index using weights while avoiding Hypothesis bias.

        Shuffles indices before weighted selection to ensure fair distribution.
        The shuffle uses the random source but decouples the selection from
        index ordering, so Hypothesis's bias toward small values doesn't
        cause preference for early indices.
        """
        n = len(variant_keys)
        with self._lock:
            weights = [self._get_weight_unlocked(k) for k in variant_keys]

        # Shuffle indices to decouple selection from original ordering.
        # Even if Hypothesis biases toward selecting index 0 after shuffle,
        # the shuffled order is different each draw, preventing systematic bias.
        indices = list(range(n))
        random.shuffle(indices)

        # Build shuffled weights
        shuffled_weights = [weights[i] for i in indices]
        total = sum(shuffled_weights)

        if total == 0:
            # All weights zero (all recently used), pick first shuffled
            return indices[0]

        # Weighted selection from shuffled indices
        # Even with Hypothesis's bias toward small cumulative values,
        # the shuffled order ensures different variants get picked
        r = random.random() * total
        cumulative = 0.0
        for i, w in enumerate(shuffled_weights):
            cumulative += w
            if r < cumulative:
                return indices[i]

        return indices[-1]

    def _get_weight_unlocked(self, variant_key: str) -> float:
        """Get weight without acquiring lock (caller must hold lock)."""
        last_step = self._last_drawn.get(variant_key)
        if last_step is None:
            return 1.0
        age = self._step - last_step
        return age / (age + RECENCY_DECAY_FACTOR)

    def record_draw(self, variant_key: str) -> None:
        """Record that a variant was drawn, advancing the global step."""
        with self._lock:
            self._step += 1
            # Delete first to move to end (maintains LRU order)
            if variant_key in self._last_drawn:
                del self._last_drawn[variant_key]
            self._last_drawn[variant_key] = self._step
            # Evict oldest if over limit
            while len(self._last_drawn) > self._maxlen:
                del self._last_drawn[next(iter(self._last_drawn))]


@dataclass(slots=True, frozen=True)
class ParameterRequirement:
    resource_name: str
    resource_field: str


def build_parameter_requirements(graph: DependencyGraph) -> dict[RequirementKey, ParameterRequirement]:
    """Index resource inputs by operation / location / parameter name."""
    requirements: dict[RequirementKey, ParameterRequirement] = {}
    for label, operation in graph.operations.items():
        for slot in operation.inputs:
            if not isinstance(slot.parameter_name, str) or slot.resource_field is None:
                continue
            key = (label, slot.parameter_location, slot.parameter_name)
            requirements[key] = ParameterRequirement(
                resource_name=slot.resource.name, resource_field=slot.resource_field
            )
    return requirements


@dataclass(slots=True)
class OpenApiExtraDataSource(ExtraDataSource):
    """Provides extra data from captured API responses to augment parameter schemas."""

    repository: ResourceRepository
    requirements: dict[RequirementKey, ParameterRequirement]
    usage_tracker: VariantUsageTracker

    def __init__(
        self,
        repository: ResourceRepository,
        requirements: dict[RequirementKey, ParameterRequirement],
        usage_tracker: VariantUsageTracker | None = None,
    ) -> None:
        self.repository = repository
        self.requirements = requirements
        self.usage_tracker = usage_tracker if usage_tracker is not None else VariantUsageTracker()

    def get_captured_variants(
        self,
        *,
        operation: APIOperation,
        location: ParameterLocation,
        schema: JsonSchema,
    ) -> list[dict[str, Any]] | None:
        """Get captured variants for hybrid strategy.

        Returns list of parameter value sets from captured responses.
        For single requirements, returns single-property dicts.
        For multiple requirements, returns complete value sets preserving relationships.
        """
        if not isinstance(schema, dict):
            return None

        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return None

        # Collect requirements for this schema
        property_requirements: dict[str, ParameterRequirement] = {}
        for name in properties:
            requirement = self.requirements.get((operation.label, location, name))
            if requirement is not None:
                property_requirements[name] = requirement

        if not property_requirements:
            return None

        # Single requirement: return simple single-property variants
        if len(property_requirements) == 1:
            name, requirement = next(iter(property_requirements.items()))
            values = self._collect_values(requirement)
            if not values:
                return None
            return [{name: value} for value in values]

        # Multiple requirements: return complete object variants preserving relationships
        return self._collect_object_variants(property_requirements) or None

    def _collect_object_variants(self, requirements: dict[str, ParameterRequirement]) -> list[dict[str, Any]]:
        """Collect complete value sets that preserve relationships between properties."""
        # Get all resource types involved
        resource_names = {req.resource_name for req in requirements.values()}

        variants: list[dict[str, Any]] = []
        seen: set[str] = set()

        # For each resource instance, try to build a complete variant
        for resource_name in resource_names:
            for instance in self.repository.iter_instances(resource_name):
                variant: dict[str, Any] = {}

                for param_name, req in requirements.items():
                    if req.resource_name == resource_name:
                        # Value from the resource data (e.g., Pet.id from response)
                        value = instance.data.get(req.resource_field)
                    else:
                        # Value from context (e.g., ownerId from request path)
                        value = instance.context.get(param_name)

                    if value is not None:
                        variant[param_name] = value

                # Only include if we filled ALL requirements
                if len(variant) == len(requirements):
                    # Deduplicate by serializing the variant
                    key = json.dumps(variant, sort_keys=True, default=str)
                    if key not in seen:
                        seen.add(key)
                        variants.append(variant)

        return variants

    def _collect_values(self, requirement: ParameterRequirement) -> list[Any]:
        """Collect unique values from captured resource instances."""
        instances = self.repository.iter_instances(requirement.resource_name)
        values: list[Any] = []
        seen: set[DedupKey] = set()

        for instance in instances:
            value = instance.data.get(requirement.resource_field, NOT_SET)
            if value is NOT_SET:
                continue

            dedup_key: DedupKey
            if isinstance(value, (str, int, float, bool)) or value is None:
                dedup_key = (type(value), value)
            else:
                try:
                    serialized = json.dumps(value, sort_keys=True, default=str)
                    dedup_key = (type(value), serialized)
                except (TypeError, ValueError):
                    continue

            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            values.append(value)

        return values

    def should_record(self, *, operation: str) -> bool:
        """Check if responses should be recorded for this operation."""
        return bool(self.repository.descriptors_for_operation(operation))

    def record_response(
        self,
        *,
        operation: APIOperation,
        response: Response,
        case: Case,
    ) -> None:
        """Record a response for later use in test generation.

        Handles deserialization and extraction of response data internally.
        """
        content_types = response.headers.get("content-type")
        if not content_types:
            return
        content_type = content_types[0]
        context = deserialization.DeserializationContext(operation=operation, case=case)
        try:
            payload = deserialization.deserialize_response(response, content_type, context=context)
        except (TypeError, ValueError, json.JSONDecodeError, NotImplementedError):
            return
        self.repository.record_response(
            operation=operation.label,
            status_code=response.status_code,
            payload=payload,
            context=case.path_parameters or {},
        )
