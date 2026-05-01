from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from schemathesis.core.error_feedback.store import ErrorFeedbackStore, Observation, ObservationKind
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
        """Return a new schema reflecting the observations. Must not mutate the input."""
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
    current = schema
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
    if not path:
        return
    *prefix, leaf = path
    current = schema
    for step in prefix:
        # Array indices in nested paths aren't supported yet — server messages we parse
        # are field-name based, not item-index based.
        if not isinstance(step, str):
            return
        properties = current.setdefault("properties", {})
        nested = properties.get(step)
        if not isinstance(nested, dict):
            nested = {"type": "object", "properties": {}, "required": []}
            properties[step] = nested
        current = nested
    if isinstance(leaf, str):
        _ensure_required_in_object(current, leaf)


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

        # Deep clone: callers cache the input schema; we must never mutate it in place.
        result = deepclone(schema)

        targets: list[dict[str, Any]] = []
        if _is_object_schema(result):
            targets.append(result)
        for keyword in ("oneOf", "anyOf", "allOf"):
            branches = result.get(keyword)
            if isinstance(branches, list):
                for branch in branches:
                    if isinstance(branch, dict) and _is_object_schema(branch):
                        targets.append(branch)

        if not targets:
            return result

        for observation in observations:
            for target in targets:
                _walk_and_apply(target, observation.parameter_path)

        return result
