from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from schemathesis.core.error_feedback.store import (
    BoundDirection,
    EnumPayload,
    ErrorFeedbackStore,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    PatternPayload,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.jsonschema import maybe_resolve_bundled
from schemathesis.core.jsonschema.types import JsonSchema, get_type
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.registries import Registry
from schemathesis.core.transforms import deepclone
from schemathesis.specs.openapi.adapter import v3_1
from schemathesis.specs.openapi.patterns import is_valid_python_regex, normalize_regex

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
        operation: APIOperation,
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
    schema = maybe_resolve_bundled(schema)
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
    """Descend along `path` and mark the leaf as required.

    String-only paths only — synthesizing intermediate objects under `properties`
    is safe but synthesizing array `items` requires knowing the element shape,
    which we don't have. Jackson never drives required-field adjustments anyway.
    """
    if not path or not all(isinstance(step, str) for step in path):
        return
    *prefix, leaf = path
    assert isinstance(leaf, str)
    current = schema
    for step in prefix:
        assert isinstance(step, str)
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
        if payload.min is not None:
            existing_min = prop.get(min_keyword)
            if not isinstance(existing_min, int) or payload.min > existing_min:
                prop[min_keyword] = payload.min
        if payload.max is not None:
            existing_max = prop.get(max_keyword)
            if not isinstance(existing_max, int) or payload.max < existing_max:
                prop[max_keyword] = payload.max


def _walk_to_property(schema: dict[str, Any], path: tuple[str | int, ...]) -> dict[str, Any] | None:
    """Descend `schema` along `path`; return the leaf prop dict or None.

    String steps navigate `properties[<name>]` (object properties); int steps
    navigate `items` (array elements). The index value itself is irrelevant for
    JSON Schema's uniform-items model — every element shares the `items` schema.
    """
    if not path:
        return None
    current = schema
    for step in path:
        if isinstance(step, str):
            properties = current.get("properties")
            if not isinstance(properties, dict):
                return None
            nested = properties.get(step)
        else:
            nested = current.get("items")
        if not isinstance(nested, dict):
            return None
        current = nested
    return current


def _apply_format_to_property(prop: dict[str, Any], name: str) -> None:
    """Set `format: <name>` on a string property when none is already declared."""
    if "string" not in get_type(prop):
        return
    if "format" in prop:
        return
    prop["format"] = name


@ADJUSTMENTS.register
class FormatAdjustment:
    """Inject `format` onto string properties when the server reveals it via 4xx.

    Writes `format: <name>` to the resolved property only when it's a string
    (or includes string in a type union) and doesn't already declare a format.
    """

    handles = frozenset({ObservationKind.FORMAT})

    def apply(
        self,
        *,
        operation: APIOperation,
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
            assert isinstance(observation.payload, FormatPayload)
            for target in targets:
                prop = _walk_to_property(target, observation.parameter_path)
                if prop is not None:
                    _apply_format_to_property(prop, observation.payload.name)

        return schema


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
        operation: APIOperation,
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


def _coerce_numeric_bound(value: float, types: list[str]) -> int | float:
    """Emit integers when the property is integer-typed and the bound is integral."""
    if "integer" in types and "number" not in types and value.is_integer():
        return int(value)
    return value


def _apply_numeric_bound_to_property(
    prop: dict[str, Any],
    payload: NumericBoundPayload,
    *,
    is_2020_12: bool,
) -> None:
    """Write one numeric bound onto `prop`; skip when an existing constraint covers this direction."""
    types = get_type(prop)
    if "number" not in types and "integer" not in types:
        return
    bound = _coerce_numeric_bound(payload.bound, types)
    if payload.direction is BoundDirection.MIN:
        if "minimum" in prop or "exclusiveMinimum" in prop:
            return
        if is_2020_12 and payload.exclusive:
            prop["exclusiveMinimum"] = bound
        else:
            prop["minimum"] = bound
            if payload.exclusive:
                prop["exclusiveMinimum"] = True
    else:
        if "maximum" in prop or "exclusiveMaximum" in prop:
            return
        if is_2020_12 and payload.exclusive:
            prop["exclusiveMaximum"] = bound
        else:
            prop["maximum"] = bound
            if payload.exclusive:
                prop["exclusiveMaximum"] = True


@ADJUSTMENTS.register
class NumericBoundAdjustment:
    """Apply server-reported numeric bounds, picking the keyword shape from the operation's draft."""

    handles = frozenset({ObservationKind.NUMERIC_BOUND})

    def apply(
        self,
        *,
        operation: APIOperation,
        location: ParameterLocation,
        schema: JsonSchema,
        observations: tuple[Observation, ...],
    ) -> JsonSchema:
        if not isinstance(schema, dict):
            return schema

        targets = _collect_object_targets(schema)
        if not targets:
            return schema

        from schemathesis.specs.openapi.schemas import OpenApiSchema

        assert isinstance(operation.schema, OpenApiSchema)
        is_2020_12 = operation.schema.adapter is v3_1
        for observation in observations:
            assert isinstance(observation.payload, NumericBoundPayload)
            for target in targets:
                prop = _walk_to_property(target, observation.parameter_path)
                if prop is not None:
                    _apply_numeric_bound_to_property(prop, observation.payload, is_2020_12=is_2020_12)

        return schema


def _apply_pattern_to_property(prop: dict[str, Any], regex: str) -> None:
    """Write `pattern: <regex>` on a string property when none is already declared."""
    if "string" not in get_type(prop):
        return
    if "pattern" in prop:
        return
    # `is_valid_python_regex` accepts `\A`/`\Z` (Python-only anchors that
    # `jsonschema_rs` rejects), so route those through `normalize_regex` too.
    needs_translation = not is_valid_python_regex(regex) or regex.startswith("\\A") or regex.endswith("\\Z")
    if not needs_translation:
        prop["pattern"] = regex
        return
    translated = normalize_regex(regex)
    if translated is not None:
        prop["pattern"] = translated


@ADJUSTMENTS.register
class PatternAdjustment:
    """Inject `pattern` onto string properties when the server reveals it via `@Pattern` 4xx."""

    handles = frozenset({ObservationKind.PATTERN})

    def apply(
        self,
        *,
        operation: APIOperation,
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
            assert isinstance(observation.payload, PatternPayload)
            for target in targets:
                prop = _walk_to_property(target, observation.parameter_path)
                if prop is not None:
                    _apply_pattern_to_property(prop, observation.payload.regex)

        return schema


# Java-type-to-JSON-Schema-format map. Covers the standard `java.time` and
# `java.net`/`java.util` types; unknown types are skipped at the consumer level.
_JAVA_TYPE_TO_FORMAT: dict[str, str] = {
    "java.time.LocalDate": "date",
    "java.time.LocalDateTime": "date-time",
    "java.time.Instant": "date-time",
    "java.time.OffsetDateTime": "date-time",
    "java.time.ZonedDateTime": "date-time",
    "java.util.Date": "date-time",
    "java.util.UUID": "uuid",
    "java.net.URI": "uri",
    "java.net.URL": "uri",
}


@ADJUSTMENTS.register
class TypeMismatchAdjustment:
    """Inject `format` from a Jackson type-error message via the Java-type-to-format map."""

    handles = frozenset({ObservationKind.TYPE_MISMATCH})

    def apply(
        self,
        *,
        operation: APIOperation,
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
            assert isinstance(observation.payload, TypeMismatchPayload)
            format_name = _JAVA_TYPE_TO_FORMAT.get(observation.payload.java_type)
            if format_name is None:
                continue
            for target in targets:
                prop = _walk_to_property(target, observation.parameter_path)
                if prop is not None:
                    _apply_format_to_property(prop, format_name)

        return schema


def _apply_enum_to_property(prop: dict[str, Any], values: tuple[str, ...]) -> None:
    """Set `enum: [...]` on a string property when none is already declared."""
    if "string" not in get_type(prop):
        return
    if "enum" in prop:
        return
    prop["enum"] = list(values)


@ADJUSTMENTS.register
class EnumAdjustment:
    """Inject `enum` from a Jackson enum-deserialization message onto the resolved property."""

    handles = frozenset({ObservationKind.ENUM})

    def apply(
        self,
        *,
        operation: APIOperation,
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
            assert isinstance(observation.payload, EnumPayload)
            for target in targets:
                prop = _walk_to_property(target, observation.parameter_path)
                if prop is not None:
                    _apply_enum_to_property(prop, observation.payload.values)

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
        operation: APIOperation,
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
