from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

import jsonschema_rs

from schemathesis.core import NOT_SET, deserialization
from schemathesis.core.errors import InvalidSchema, MalformedMediaType
from schemathesis.core.jsonschema import make_validator, schema_with_bundle
from schemathesis.core.jsonschema.bundler import BundleError
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.resources import ExtraDataSource, PoolDraw, PoolPick
from schemathesis.resources.repository import ResourceInstance, ResourceRepository
from schemathesis.specs.openapi.semantic_pool import SemanticValueIndex, iter_ingestion_leaves
from schemathesis.specs.openapi.stateful.dependencies.models import DependencyGraph, InputSlot
from schemathesis.specs.openapi.stateful.dependencies.naming import normalize_for_matching

if TYPE_CHECKING:
    from random import Random

    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.schemas import OpenApiOperation

RequirementKey = tuple[str, ParameterLocation, str]
DedupKey: TypeAlias = tuple[type, str | int | float | bool | None]

# Decay factor for recency weighting. Higher = faster recovery of weight.
RECENCY_DECAY_FACTOR = 3.0
# Maximum number of variants to track. Oldest entries are evicted when exceeded.
MAX_TRACKED_VARIANTS = 10000
# Decay factor for delete attempts. Weight = decay ^ attempts.
# 0.3 gives: 0 attempts = 1.0, 1 = 0.3, 2 = 0.09, 3 = 0.027
DELETE_ATTEMPT_DECAY = 0.3


class VariantUsageTracker:
    """Tracks variant usage for weighted sampling.

    Maintains a global step counter and records when each variant was last drawn.
    Recently drawn variants get lower weights to encourage diversity.
    Also tracks DELETE attempts per variant to spread deletions across resources.
    Uses LRU eviction to bound memory usage.
    """

    __slots__ = ("_step", "_last_drawn", "_delete_attempts", "_maxlen", "_lock")

    def __init__(self, maxlen: int = MAX_TRACKED_VARIANTS) -> None:
        self._step = 0
        self._last_drawn: dict[str, int] = {}
        self._delete_attempts: dict[str, int] = {}
        self._maxlen = maxlen
        self._lock = threading.Lock()

    def weighted_select(self, variant_keys: list[str], random: Random) -> int:
        """Select a variant index using weights while avoiding Hypothesis bias.

        Shuffles indices before weighted selection to ensure fair distribution.
        The shuffle uses the random source but decouples the selection from
        index ordering, so Hypothesis's bias toward small values doesn't
        cause preference for early indices.

        Weights combine:
        - Recency: recently drawn variants get lower weight (recovers over time)
        - Delete attempts: variants targeted for deletion get permanently lower weight
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
        """Get weight without acquiring lock (caller must hold lock).

        Combines recency weighting with delete attempt decay:
        - Recency: recently drawn variants get lower weight (recovers over time)
        - Delete attempts: variants targeted for deletion get exponentially lower weight (permanent)
        """
        # Recency-based weight (recovers over time)
        last_step = self._last_drawn.get(variant_key)
        if last_step is None:
            recency_weight = 1.0
        else:
            age = self._step - last_step
            recency_weight = age / (age + RECENCY_DECAY_FACTOR)

        # Delete attempt decay (permanent, doesn't recover)
        delete_attempts = self._delete_attempts.get(variant_key, 0)
        delete_weight = DELETE_ATTEMPT_DECAY**delete_attempts

        return recency_weight * delete_weight

    def argmax_by_weight(self, variant_keys: list[str]) -> int:
        """Return the index of the highest-weight variant; ties broken by lowest index."""
        with self._lock:
            weights = [self._get_weight_unlocked(k) for k in variant_keys]
        best_idx = 0
        best_weight = weights[0]
        for i in range(1, len(weights)):
            if weights[i] > best_weight:
                best_idx = i
                best_weight = weights[i]
        return best_idx

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

    def record_successful_delete(self, variant_key: str) -> None:
        """Record that a resource was successfully deleted.

        Increments a permanent counter that reduces the variant's selection weight.
        Unlike recency, this doesn't recover over time - deleted resources stay deprioritized.
        """
        with self._lock:
            self._delete_attempts[variant_key] = self._delete_attempts.get(variant_key, 0) + 1


@dataclass(slots=True, frozen=True)
class ParameterRequirement:
    resource_name: str
    resource_field: str


@dataclass(slots=True, frozen=True)
class CapturedVariant:
    """A captured pool overlay together with the provenance of every slot it fills.

    `overlay` is the dict that gets deep-merged into the generated case body; `draws`
    records which captured `ResourceInstance` supplied each slot value, so the analyzer
    can attribute the case to its semantic predecessor(s).
    """

    overlay: dict[str, Any]
    draws: tuple[PoolDraw, ...]


def _build_pool_draw(slot: InputSlot, instance: ResourceInstance) -> PoolDraw:
    """Build a `PoolDraw` provenance record for a slot that was filled from `instance`."""
    parameter_name = slot.parameter_name if isinstance(slot.parameter_name, str) else ""
    return PoolDraw(
        location=slot.parameter_location.value,
        parameter_name=parameter_name,
        resource_name=slot.resource.name,
        resource_field=slot.resource_field or "",
        source_operation=instance.source_operation,
        source_status=instance.status_code,
    )


def _build_pool_draw_from_requirement(
    *,
    location: ParameterLocation,
    parameter_name: str,
    requirement: ParameterRequirement,
    instance: ResourceInstance,
) -> PoolDraw:
    """Variant of `_build_pool_draw` for sites that hold a `ParameterRequirement` rather than an `InputSlot`."""
    return PoolDraw(
        location=location.value,
        parameter_name=parameter_name,
        resource_name=requirement.resource_name,
        resource_field=requirement.resource_field,
        source_operation=instance.source_operation,
        source_status=instance.status_code,
    )


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


def build_inputs_by_label(graph: DependencyGraph) -> dict[str, list[InputSlot]]:
    """Index input slots by operation label for runtime lookup.

    Only operations with at least one resource-bound slot are recorded; this
    keeps the dict small and lets `should_record_request` short-circuit cheaply.
    """
    inputs_by_label: dict[str, list[InputSlot]] = {}
    for label, operation in graph.operations.items():
        slots = [slot for slot in operation.inputs if slot.resource_field is not None]
        if slots:
            inputs_by_label[label] = slots
    return inputs_by_label


def _build_property_validator(
    prop_schema: object, container_schema: JsonSchema, validator_cls: type
) -> jsonschema_rs.Validator | None:
    """Validator for a single property, splicing the container's `x-bundled` for `$ref` resolution."""
    if not isinstance(prop_schema, dict):
        return None
    try:
        return make_validator(schema_with_bundle(prop_schema, container_schema), validator_cls)
    except jsonschema_rs.ValidationError:
        # Malformed property schema (e.g. invalid regex, unsupported keyword combination).
        return None


@dataclass(slots=True, frozen=True)
class _VariantSlot:
    # `path` is where the value lands in the variant; `lookup_key` is the slash-joined
    # form used as the requirements-dict key and `instance.context` lookup.
    path: tuple[str, ...]
    leaf_schema: JsonSchema
    requirement: ParameterRequirement

    @property
    def lookup_key(self) -> str:
        return "/".join(self.path)


def _assemble_path(path: tuple[str, ...], value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    cursor = result
    for key in path[:-1]:
        nxt: dict[str, Any] = {}
        cursor[key] = nxt
        cursor = nxt
    cursor[path[-1]] = value
    return result


def _assemble_variant(slots: list[_VariantSlot], values_by_lookup_key: dict[str, Any]) -> dict[str, Any]:
    variant: dict[str, Any] = {}
    for slot in slots:
        if slot.lookup_key not in values_by_lookup_key:
            continue
        cursor = variant
        for key in slot.path[:-1]:
            cursor = cursor.setdefault(key, {})
        cursor[slot.path[-1]] = values_by_lookup_key[slot.lookup_key]
    return variant


def _variant_satisfies_paths(
    variant: dict[str, Any],
    slots: list[_VariantSlot],
    validators: dict[str, jsonschema_rs.Validator | None],
) -> bool:
    for slot in slots:
        validator = validators.get(slot.lookup_key)
        if validator is None:
            continue
        cursor: Any = variant
        for key in slot.path:
            if not isinstance(cursor, dict) or key not in cursor:
                cursor = None
                break
            cursor = cursor[key]
        if cursor is None:
            continue
        if not validator.is_valid(cursor):
            return False
    return True


@dataclass(slots=True)
class OpenApiExtraDataSource(ExtraDataSource):
    """Provides extra data from captured API responses to augment parameter schemas."""

    repository: ResourceRepository
    requirements: dict[RequirementKey, ParameterRequirement]
    inputs_by_label: dict[str, list[InputSlot]]
    usage_tracker: VariantUsageTracker
    semantic_index: SemanticValueIndex | None
    semantic_eligible_operations: frozenset[str]
    consumer_labels: frozenset[str]

    def __init__(
        self,
        repository: ResourceRepository,
        requirements: dict[RequirementKey, ParameterRequirement],
        inputs_by_label: dict[str, list[InputSlot]] | None = None,
        usage_tracker: VariantUsageTracker | None = None,
        semantic_index: SemanticValueIndex | None = None,
        semantic_eligible_operations: frozenset[str] = frozenset(),
    ) -> None:
        self.repository = repository
        self.requirements = requirements
        self.inputs_by_label = inputs_by_label if inputs_by_label is not None else {}
        self.usage_tracker = usage_tracker if usage_tracker is not None else VariantUsageTracker()
        self.semantic_index = semantic_index
        self.semantic_eligible_operations = semantic_eligible_operations
        # Operations whose strategies bind captured variants at build time (consumer side).
        self.consumer_labels: frozenset[str] = frozenset(key[0] for key in requirements)
        # Values that have been successfully DELETEd; pool draws skip them.
        self._tombstoned: set[tuple[str, Any]] = set()

    def _is_tombstoned(self, resource_name: str, value: Any) -> bool:
        try:
            return (resource_name, value) in self._tombstoned
        except TypeError:  # unhashable value
            return False

    def get_captured_variants(
        self,
        *,
        operation: OpenApiOperation,
        location: ParameterLocation,
        schema: JsonSchema,
    ) -> list[CapturedVariant] | None:
        """Get captured variants for hybrid strategy, each carrying its own provenance.

        For single requirements, each variant has a single-property overlay.
        For multiple requirements, each variant has a complete value set preserving relationships.
        For BODY location, walks one level into nested objects so nested foreign-key fields get overlays.
        """
        if not isinstance(schema, dict):
            return None

        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return None

        slots: list[_VariantSlot] = []
        for name, prop_schema in properties.items():
            requirement = self.requirements.get((operation.label, location, name))
            if requirement is not None:
                slots.append(_VariantSlot((name,), prop_schema, requirement))
                continue
            if location != ParameterLocation.BODY or not isinstance(prop_schema, dict):
                continue
            sub_props = prop_schema.get("properties")
            if not isinstance(sub_props, dict):
                continue
            for sub_name, sub_schema in sub_props.items():
                req = self.requirements.get((operation.label, location, f"{name}/{sub_name}"))
                if req is not None:
                    slots.append(_VariantSlot((name, sub_name), sub_schema, req))

        if not slots:
            return None

        variants: list[CapturedVariant]
        if len(slots) == 1:
            slot = slots[0]
            variants = [
                CapturedVariant(
                    overlay=_assemble_path(slot.path, value),
                    draws=(
                        _build_pool_draw_from_requirement(
                            location=location,
                            parameter_name=slot.lookup_key,
                            requirement=slot.requirement,
                            instance=instance,
                        ),
                    ),
                )
                for value, instance in self._collect_values(slot.requirement)
            ]
        else:
            variants = self._collect_object_variants(slots, location)

        validator_cls = operation.schema.adapter.jsonschema_validator_cls
        validators = {
            slot.lookup_key: _build_property_validator(slot.leaf_schema, schema, validator_cls) for slot in slots
        }
        variants = [v for v in variants if _variant_satisfies_paths(v.overlay, slots, validators)]
        return variants or None

    def _collect_object_variants(self, slots: list[_VariantSlot], location: ParameterLocation) -> list[CapturedVariant]:
        """Collect complete value sets that preserve relationships between properties."""
        resource_names = {slot.requirement.resource_name for slot in slots}

        variants: list[CapturedVariant] = []
        seen: set[str] = set()

        for resource_name in resource_names:
            for instance in self.repository.iter_instances(resource_name):
                filled: dict[str, Any] = {}
                # Same-resource slots get attribution to `instance`; context-only slots also do,
                # because `instance.context` is captured when this instance was created.
                draws: list[PoolDraw] = []
                for slot in slots:
                    req = slot.requirement
                    if req.resource_name == resource_name:
                        value = instance.data.get(req.resource_field)
                    else:
                        value = instance.context.get(slot.lookup_key)
                    if value is not None and not self._is_tombstoned(req.resource_name, value):
                        filled[slot.lookup_key] = value
                        draws.append(
                            _build_pool_draw_from_requirement(
                                location=location,
                                parameter_name=slot.lookup_key,
                                requirement=req,
                                instance=instance,
                            )
                        )
                if len(filled) == len(slots):
                    overlay = _assemble_variant(slots, filled)
                    key = jsonschema_rs.canonical.json.to_string(overlay)
                    if key not in seen:
                        seen.add(key)
                        variants.append(CapturedVariant(overlay=overlay, draws=tuple(draws)))

        if variants:
            return variants

        # No instance covered every slot; chain picks across resources, each constrained
        # by the context of slots already chosen.
        chosen: dict[str, Any] = {}
        chained_draws: list[PoolDraw] = []
        for slot in slots:
            req = slot.requirement
            best: tuple[Any, ResourceInstance] | None = None
            for instance in self.repository.iter_instances(req.resource_name):
                value = instance.data.get(req.resource_field) if req.resource_name else None
                if value is None:
                    continue
                if self._is_tombstoned(req.resource_name, value):
                    continue
                if any(instance.context.get(k) not in (None, v) for k, v in chosen.items()):
                    continue
                best = (value, instance)
                break
            if best is not None:
                chosen[slot.lookup_key] = best[0]
                chained_draws.append(
                    _build_pool_draw_from_requirement(
                        location=location,
                        parameter_name=slot.lookup_key,
                        requirement=req,
                        instance=best[1],
                    )
                )
        if chosen:
            variants.append(CapturedVariant(overlay=_assemble_variant(slots, chosen), draws=tuple(chained_draws)))
        return variants

    def _collect_values(self, requirement: ParameterRequirement) -> list[tuple[Any, ResourceInstance]]:
        """Collect unique non-tombstoned values + their source instances from captured responses."""
        instances = self.repository.iter_instances(requirement.resource_name)
        values: list[tuple[Any, ResourceInstance]] = []
        seen: set[DedupKey] = set()

        for instance in instances:
            value = instance.data.get(requirement.resource_field, NOT_SET)
            if value is NOT_SET:
                continue
            if self._is_tombstoned(requirement.resource_name, value):
                continue

            dedup_key: DedupKey
            if isinstance(value, str | int | float | bool) or value is None:
                dedup_key = (type(value), value)
            else:
                try:
                    serialized = jsonschema_rs.canonical.json.to_string(value)
                    dedup_key = (type(value), serialized)
                except (TypeError, ValueError):
                    continue

            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            values.append((value, instance))

        return values

    def pick_captured_value(
        self,
        *,
        operation: APIOperation,
        location: ParameterLocation,
        name: str,
        context_constraints: dict[str, Any] | None = None,
    ) -> Any | None:
        """Return one weighted-selected pool value for a resource-bound parameter, or None."""
        picked = self._pick_captured_with_provenance(
            operation=operation,
            location=location,
            name=name,
            context_constraints=context_constraints,
        )
        return picked[0] if picked is not None else None

    def _pick_captured_with_provenance(
        self,
        *,
        operation: APIOperation,
        location: ParameterLocation,
        name: str,
        context_constraints: dict[str, Any] | None = None,
    ) -> tuple[Any, ResourceInstance] | None:
        """Like `pick_captured_value` but also returns the `ResourceInstance` whose draw won.

        `context_constraints` keeps draws on the same parent chain; missing context keys
        match anything, and the filter falls through when no constrained instance exists.
        """
        requirement = self.requirements.get((operation.label, location, name))
        if requirement is None:
            return None
        all_candidates: list[tuple[ResourceInstance, Any]] = []
        constrained: list[tuple[ResourceInstance, Any]] = []
        for instance in self.repository.iter_instances(requirement.resource_name):
            value = instance.data.get(requirement.resource_field)
            if value is None:
                continue
            if self._is_tombstoned(requirement.resource_name, value):
                continue
            all_candidates.append((instance, value))
            if context_constraints and any(
                instance.context.get(k) not in (None, v) for k, v in context_constraints.items()
            ):
                continue
            constrained.append((instance, value))
        candidates = constrained or all_candidates
        if not candidates:
            return None
        variant_keys = [jsonschema_rs.canonical.json.to_string(instance.data) for instance, _ in candidates]
        idx = self.usage_tracker.argmax_by_weight(variant_keys)
        self.usage_tracker.record_draw(variant_keys[idx])
        chosen_instance, chosen_value = candidates[idx]
        return chosen_value, chosen_instance

    def pick_correlated_values(
        self,
        *,
        operation: APIOperation,
    ) -> PoolPick:
        """Return one (location, name) -> value map keeping all resource-bound slots correlated.

        The returned `PoolPick.draws` carries one `PoolDraw` per slot that was filled from the pool,
        recording which captured `ResourceInstance` supplied the value.
        """
        slots: list[InputSlot] = []
        for slot in self.inputs_by_label.get(operation.label, ()):
            if slot.resource_field is None or not isinstance(slot.parameter_name, str):
                continue
            slots.append(slot)
        if not slots:
            return PoolPick()

        resource_names = {slot.resource.name for slot in slots}
        # Each "satisfying" entry records the seed instance and per-slot (instance, value) so
        # provenance stays attached even when context-only fields come from a different parent.
        satisfying: list[
            tuple[ResourceInstance, dict[tuple[ParameterLocation, str], tuple[ResourceInstance, Any]]]
        ] = []

        for resource_name in resource_names:
            for instance in self.repository.iter_instances(resource_name):
                filled: dict[tuple[ParameterLocation, str], tuple[ResourceInstance, Any]] = {}
                for slot in slots:
                    param_name = slot.parameter_name
                    resource_field = slot.resource_field
                    assert isinstance(param_name, str)
                    assert resource_field is not None
                    if slot.resource.name == resource_name:
                        value = instance.data.get(resource_field)
                    else:
                        value = instance.context.get(param_name)
                    if value is None:
                        break
                    # Same-resource slots and context-only slots both attribute to `instance`:
                    # parent ids are stored on the child's context when the instance was captured,
                    # so a single instance accounts for the whole correlated pick.
                    filled[(slot.parameter_location, param_name)] = (instance, value)
                else:
                    satisfying.append((instance, filled))

        if satisfying:
            variant_keys = [jsonschema_rs.canonical.json.to_string(inst.data) for inst, _ in satisfying]
            idx = self.usage_tracker.argmax_by_weight(variant_keys)
            self.usage_tracker.record_draw(variant_keys[idx])
            chosen = satisfying[idx][1]
            slot_by_key = {(slot.parameter_location, slot.parameter_name): slot for slot in slots}
            values = {key: value for key, (_, value) in chosen.items()}
            draws = tuple(
                _build_pool_draw(slot_by_key[key], source_instance) for key, (source_instance, _) in chosen.items()
            )
            return PoolPick(values=values, draws=draws)

        # Independent picks with chained context constraints so child resources track parent.
        values_result: dict[tuple[ParameterLocation, str], Any] = {}
        draws_result: list[PoolDraw] = []
        misses_result: list[tuple[str, str]] = []
        context_constraints: dict[str, Any] = {}
        for slot in slots:
            param_name = slot.parameter_name
            assert isinstance(param_name, str)
            picked = self._pick_captured_with_provenance(
                operation=operation,
                location=slot.parameter_location,
                name=param_name,
                context_constraints=context_constraints,
            )
            if picked is not None:
                value, source_instance = picked
                values_result[(slot.parameter_location, param_name)] = value
                draws_result.append(_build_pool_draw(slot, source_instance))
                context_constraints[param_name] = value
            else:
                misses_result.append((slot.parameter_location.value, param_name))
        return PoolPick(values=values_result, draws=tuple(draws_result), misses=tuple(misses_result))

    def should_record(self, *, operation: str) -> bool:
        """Check if responses should be recorded for this operation."""
        if self.repository.descriptors_for_operation(operation):
            return True
        if self.semantic_index is not None and operation in self.semantic_eligible_operations:
            return True
        return False

    def should_record_request(self, *, operation: str) -> bool:
        """Check if request inputs should be captured for this operation."""
        return operation in self.inputs_by_label

    def record_request(
        self,
        *,
        operation: APIOperation,
        case: Case,
        status_code: int,
    ) -> None:
        """Capture path-parameter and body-field values from a successful request."""
        slots = self.inputs_by_label.get(operation.label)
        if not slots:
            return
        if case.meta is not None:
            slots = [
                slot
                for slot in slots
                if (component := case.meta.components.get(slot.parameter_location)) is None
                or component.mode == GenerationMode.POSITIVE
            ]
            if not slots:
                return
        self.repository.record_request(
            operation=operation.label,
            inputs=slots,
            case=case,
            status_code=status_code,
            context=case.path_parameters or {},
        )

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
        # Decide eligibility before deserializing so semantic-only operations don't pay the
        # deserialization cost (or risk a custom-deserializer exception) for non-2xx responses
        # that the pool would discard anyway.
        has_descriptors = bool(self.repository.descriptors_for_operation(operation.label))
        semantic_active = (
            self.semantic_index is not None
            and operation.label in self.semantic_eligible_operations
            and 200 <= response.status_code < 300
        )
        if not has_descriptors and not semantic_active:
            return
        content_types = response.headers.get("content-type")
        if not content_types:
            return
        content_type = content_types[0]
        context = deserialization.DeserializationContext(operation=operation, case=case)
        try:
            payload = deserialization.deserialize_response(response, content_type, context=context)
        except (TypeError, ValueError, json.JSONDecodeError, NotImplementedError):
            return
        if has_descriptors:
            self.repository.record_response(
                operation=operation.label,
                status_code=response.status_code,
                payload=payload,
                context=case.path_parameters or {},
            )
        if semantic_active:
            response_def = operation.responses.find_by_status_code(response.status_code)
            response_schema: dict[str, Any] | None = None
            if response_def is not None:
                # Recording runs outside the response-schema check path; an unresolvable
                # `$ref` or other malformed response schema must not fail the operation
                # when only non-schema checks are enabled. Fall back to a schemaless walk.
                try:
                    resolved_schema = response_def.get_schema(content_type).schema
                except (BundleError, InvalidSchema, MalformedMediaType):
                    resolved_schema = None
                # A boolean JSON Schema (true/false) carries no leaf info; treat as schemaless.
                if isinstance(resolved_schema, dict):
                    response_schema = resolved_schema
            # Normalize so a response echoing `user_id` is excluded when the path declares `userId`.
            excluded = (
                frozenset(normalize_for_matching(name) for name in case.path_parameters)
                if case.path_parameters
                else frozenset()
            )
            for leaf in iter_ingestion_leaves(response_schema, payload, excluded_names=excluded):
                self.semantic_index.add(  # type: ignore[union-attr]
                    type_token=leaf.type_token,
                    format_token=leaf.format_token,
                    pattern_hash=leaf.pattern_hash,
                    normalized_name=leaf.normalized_name,
                    value=leaf.value,
                    source_operation=operation.label,
                )

    def record_successful_delete(
        self,
        *,
        operation: APIOperation,
        case: Case,
    ) -> None:
        """Record that a resource was successfully deleted.

        This helps spread DELETE operations across different resources
        by reducing the selection weight of deleted resources.
        """
        if operation.method.lower() != "delete":
            return

        if not case.path_parameters:
            return

        # Build variant key from resource-linked path parameters.
        # This matches how variant keys are built in build_hybrid_strategy,
        # where captured variants contain only resource-linked parameters.
        resource_params = {}
        for param_name, param_value in case.path_parameters.items():
            if (operation.label, ParameterLocation.PATH, param_name) in self.requirements:
                resource_params[param_name] = param_value

        # Fall back to all path parameters for DELETE when no resource-linked params found.
        # This ensures we track successful deletes even when the dependency graph
        # doesn't link the DELETE operation's parameters to resources.
        if not resource_params:
            resource_params = dict(case.path_parameters)

        variant_key = jsonschema_rs.canonical.json.to_string(resource_params)
        self.usage_tracker.record_successful_delete(variant_key)

        # Tombstone + evict the deleted resource: subsequent pool draws skip it, and the
        # underlying entries no longer linger in the repository.
        for param_name, param_value in case.path_parameters.items():
            requirement = self.requirements.get((operation.label, ParameterLocation.PATH, param_name))
            if requirement is None:
                continue
            try:
                self._tombstoned.add((requirement.resource_name, param_value))
            except TypeError:  # unhashable value (e.g. list); fall through, eviction still runs
                pass
            self.repository.remove_by_value(requirement.resource_name, param_value)
