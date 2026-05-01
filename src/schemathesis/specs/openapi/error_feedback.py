from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from schemathesis.core.error_feedback.store import (
    ErrorFeedbackStore,
    Observation,
    ObservationKind,
    SizeBoundPayload,
)
from schemathesis.core.jsonschema.types import JsonSchema, get_type
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.registries import Registry
from schemathesis.core.transforms import deepclone

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation


class Adjustment(Protocol):
    """Mutates a JSON Schema in response to accumulated server-error observations.

    `handles` filters which observations the adjustment receives — the dispatcher
    only invokes `apply` when at least one observation's `kind` is in the set.
    """

    handles: frozenset[ObservationKind]

    def apply(
        self,
        *,
        operation: APIOperation | None,
        location: ParameterLocation,
        schema: JsonSchema,
        observations: tuple[Observation, ...],
    ) -> JsonSchema:
        """Mutate `schema` in place (or return a replacement) to reflect the observations."""
        ...  # pragma: no cover


ADJUSTMENTS: Registry[type[Adjustment]] = Registry()


def apply_adjustments(
    *,
    operation: APIOperation,
    location: ParameterLocation,
    schema: JsonSchema,
    store: ErrorFeedbackStore,
) -> JsonSchema:
    """Run every registered adjustment over `schema`, threading the result through.

    Each adjustment sees only the observations matching its `handles` set.
    """
    observations = store.observations(operation_label=operation.label, location=location)
    if not observations:
        return schema
    # Single clone shared across all adjustments.
    current: JsonSchema = deepclone(schema) if isinstance(schema, dict) else schema
    for adjustment_cls in ADJUSTMENTS.get_all():
        adjustment = adjustment_cls()
        relevant = tuple(o for o in observations if o.kind in adjustment.handles)
        if not relevant:
            continue
        current = adjustment.apply(
            operation=operation,
            location=location,
            schema=current,
            observations=relevant,
        )
    return current


def _is_object_schema(schema: dict[str, Any]) -> bool:
    # `type` may be a list (OpenAPI 3.1 / JSON Schema unions); presence of
    # `properties`/`required` is enough to treat as object when `type` is omitted.
    if "type" in schema:
        return "object" in get_type(schema)
    return "properties" in schema or "required" in schema


def _collect_object_targets(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Root + every object branch of `oneOf`/`anyOf`/`allOf` worth descending into."""
    targets: list[dict[str, Any]] = []
    if _is_object_schema(schema):
        targets.append(schema)
    for keyword in ("oneOf", "anyOf", "allOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict) and _is_object_schema(branch):
                    targets.append(branch)
    return targets


def _ensure_required_in_object(obj: dict[str, Any], leaf: str) -> None:
    """Mark `leaf` as required on `obj`, injecting a non-blank string property when absent."""
    properties = obj.setdefault("properties", {})
    existing_required = obj.get("required")
    required = existing_required if isinstance(existing_required, list) else []

    prop = properties.get(leaf)
    if not isinstance(prop, dict):
        # Field absent or declared as a boolean schema (`true`/`false`) — server
        # demands a real value, so install our non-blank string default.
        properties[leaf] = {"type": "string", "minLength": 1}
    elif "string" in get_type(prop):
        # Only tighten string constraints; never coerce a declared non-string type.
        # `get_type` handles single types, type unions (e.g. ["string", "null"]),
        # and missing `type` (treated as any-type, which covers string).
        current = prop.get("minLength")
        if not isinstance(current, int) or current < 1:
            prop["minLength"] = 1

    if leaf not in required:
        required = [*required, leaf]
    obj["required"] = required


def _walk_and_apply(schema: dict[str, Any], path: tuple[str | int, ...]) -> None:
    """Descend into `schema.properties[...]` along `path` and mark the leaf as required."""
    # Bail before any mutation when the path can't be followed: empty paths or
    # array indices (Spring parser only emits non-empty string-only paths today).
    if not path or not all(isinstance(step, str) for step in path):
        return
    *prefix, leaf = path
    assert isinstance(leaf, str)  # narrowed by the all-strings precondition above
    current = schema
    for step in prefix:
        assert isinstance(step, str)  # same precondition
        properties = current.setdefault("properties", {})
        nested = properties.get(step)
        if not isinstance(nested, dict):
            nested = {"type": "object", "properties": {}, "required": []}
            properties[step] = nested
        current = nested
    _ensure_required_in_object(current, leaf)


# Per JSON-Schema container type, the keyword pair that mirrors a Bean-validation
# `@Size`/`@Length` constraint. Numeric/boolean/null types have no length-like
# keyword and are skipped at the consumer level.
_SIZE_KEYWORDS: dict[str, tuple[str, str]] = {
    "string": ("minLength", "maxLength"),
    "array": ("minItems", "maxItems"),
    "object": ("minProperties", "maxProperties"),
}


def _apply_size_bound_to_property(prop: dict[str, Any], payload: SizeBoundPayload) -> None:
    """Layer min/max keywords onto `prop` for every applicable schema type."""
    for prop_type in get_type(prop):
        keywords = _SIZE_KEYWORDS.get(prop_type)
        if keywords is None:
            continue
        min_keyword, max_keyword = keywords
        # Tighter wins: server's bound only overrides if it's stricter than what
        # the schema already declares. This keeps user-supplied constraints intact
        # while still narrowing under-specified ones.
        existing_min = prop.get(min_keyword)
        if not isinstance(existing_min, int) or payload.min > existing_min:
            prop[min_keyword] = payload.min
        existing_max = prop.get(max_keyword)
        if not isinstance(existing_max, int) or payload.max < existing_max:
            prop[max_keyword] = payload.max


def _walk_to_property(schema: dict[str, Any], path: tuple[str | int, ...]) -> dict[str, Any] | None:
    """Descend `schema.properties[...]` along `path`; return the leaf prop dict or None."""
    if not path or not all(isinstance(step, str) for step in path):
        return None
    current = schema
    for step in path:
        properties = current.get("properties")
        if not isinstance(properties, dict):
            return None
        nested = properties.get(step)
        if not isinstance(nested, dict):
            return None
        current = nested
    return current


@ADJUSTMENTS.register
class SizeBoundAdjustment:
    """Apply server-reported `@Size`/`@Length` bounds to declared properties.

    Branches on the resolved schema's `type`: strings get `minLength`/`maxLength`,
    arrays `minItems`/`maxItems`, objects `minProperties`/`maxProperties`. Other
    types and missing properties are skipped — this adjustment narrows existing
    declarations rather than synthesising new ones.
    """

    handles = frozenset({ObservationKind.SIZE_BOUND})

    def apply(
        self,
        *,
        operation: APIOperation | None,
        location: ParameterLocation,
        schema: JsonSchema,
        observations: tuple[Observation, ...],
    ) -> JsonSchema:
        if not isinstance(schema, dict):
            return schema

        targets = _collect_object_targets(schema)
        if not targets:
            return schema

        for observation in observations:
            assert isinstance(observation.payload, SizeBoundPayload)
            for target in targets:
                prop = _walk_to_property(target, observation.parameter_path)
                if prop is not None:
                    _apply_size_bound_to_property(prop, observation.payload)

        return schema


@ADJUSTMENTS.register
class RequiredFieldAdjustment:
    """Mark fields the server told us are mandatory as `required` with `minLength: 1`.

    Applied to the root object schema and to each object branch of `oneOf`/`anyOf`/`allOf`.
    """

    handles = frozenset({ObservationKind.MUST_NOT_BE_BLANK})

    def apply(
        self,
        *,
        operation: APIOperation | None,
        location: ParameterLocation,
        schema: JsonSchema,
        observations: tuple[Observation, ...],
    ) -> JsonSchema:
        if not isinstance(schema, dict):
            return schema

        targets = _collect_object_targets(schema)
        if not targets:
            return schema

        for observation in observations:
            for target in targets:
                _walk_and_apply(target, observation.parameter_path)

        return schema
