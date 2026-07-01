"""Schema mutations."""

from __future__ import annotations

import enum
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import wraps
from typing import Any, Literal, TypeAlias, TypeVar

from hypothesis import reject
from hypothesis import strategies as st
from hypothesis.strategies._internal.featureflags import FeatureFlags, FeatureStrategy

from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.error_feedback.store import ParameterPath
from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY, get_type
from schemathesis.core.jsonschema.bundler import REFERENCE_TO_BUNDLE_PREFIX
from schemathesis.core.jsonschema.types import JsonSchemaObject, JsonValue
from schemathesis.core.media_types import is_xml
from schemathesis.core.mutations import Mutation, MutationChannel, OperatorKind
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import deepclone
from schemathesis.specs.openapi.negative.types import Draw, Schema
from schemathesis.specs.openapi.negative.utils import can_negate, is_binary_format

T = TypeVar("T")


PathKeyword: TypeAlias = Literal[
    "properties", "items", "oneOf", "anyOf", "allOf", "additionalProperties", "patternProperties"
]
PathSelector: TypeAlias = str | int | None

# Defensive cap on schema-walk recursion depth.
MAX_WALK_DEPTH = 32
# Cap on extra mutation targets per case beyond the always-chosen primary.
MAX_SECONDARY_TARGETS = 2


def _render_mutation_value(value: JsonValue, *, bare: bool) -> str:
    """Render a mutation's before/after value for display in a failure message.

    `bare=True` skips string quoting; use it for `type`-keyword mutations where the
    value is a type name (`object`, `integer`) rather than a string literal.
    """
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, str) and not bare:
        return f'"{value}"'
    return str(value)


def _render_mutation_description(mutation: Mutation) -> str:
    """Render a single mutation as `violates <keywords> [at <pointer>] [(was X[, became Y])]`."""
    keywords = ", ".join(f"`{k}`" for k in mutation.keywords)
    message = f"violates {keywords}"
    if mutation.schema_pointer:
        message += f" at {mutation.schema_pointer}"
    bare = mutation.keywords == ("type",)
    parts: list[str] = []
    if mutation.original_value is not None:
        parts.append(f"was {_render_mutation_value(mutation.original_value, bare=bare)}")
    # Dict `new_value` would dump the entire mutated body into the failure message; skip it.
    if mutation.new_value is not None and not isinstance(mutation.new_value, dict):
        parts.append(f"became {_render_mutation_value(mutation.new_value, bare=bare)}")
    if parts:
        message += f" ({', '.join(parts)})"
    return message


class MutationMetadata:
    """Per-case metadata: the structured Mutation records applied this case.

    The `description` / `parameter` / `location` properties summarize the
    single-mutation case. The `description` constructor argument lets a
    producer supply a hand-formatted string (e.g. syntax fuzzing emits
    "Invalid syntax: random bytes" directly); `NOT_SET` means "derive from
    mutations", while explicit `None` means "no description for this case".
    """

    __slots__ = ("mutations", "_description")

    mutations: tuple[Mutation, ...]
    _description: str | None | NotSet

    def __init__(
        self,
        mutations: tuple[Mutation, ...],
        description: str | None | NotSet = NOT_SET,
    ) -> None:
        self.mutations = mutations
        self._description = description

    @property
    def description(self) -> str | None:
        if not isinstance(self._description, NotSet):
            return self._description
        if not self.mutations:
            return None
        if len(self.mutations) == 1:
            return _render_mutation_description(self.mutations[0])
        return "- " + "\n- ".join(_render_mutation_description(m) for m in self.mutations)

    @property
    def parameter(self) -> str | None:
        return self.mutations[0].parameter if len(self.mutations) == 1 else None

    @property
    def location(self) -> str | None:
        if len(self.mutations) != 1:
            return None
        return self.mutations[0].schema_pointer or None


@dataclass(slots=True)
class PathStep:
    """One ancestor on the root-to-target chain for a MutationTarget."""

    # Schema dict to write `required` into during propagation.
    parent: JsonSchemaObject
    # JSON-Schema keyword the parent used to descend.
    keyword: PathKeyword
    # Child identifier within that keyword.
    selector: PathSelector


@dataclass(slots=True)
class MutationTarget:
    """A candidate mutation target plus its ancestor chain."""

    schema: JsonSchemaObject
    path: tuple[PathStep, ...]


@dataclass(frozen=True, slots=True)
class WalkStep:
    """One hop in a MutationTargetDescriptor's root-to-target walk recipe."""

    keyword: PathKeyword | Literal["$ref"]
    selector: PathSelector


@dataclass(frozen=True, slots=True)
class MutationTargetDescriptor:
    """Identity-free walk recipe for one mutation target.

    Materialization replays the walk against a per-case cloned schema to produce
    a concrete `MutationTarget` record with resolved parents. Frozen so cached
    descriptors cannot be mutated by downstream consumers.
    """

    walk: tuple[WalkStep, ...]


def compute_mutation_targets(raw_schema: JsonSchemaObject | bool) -> tuple[MutationTargetDescriptor, ...]:
    """Return walk recipes for every reachable mutation target in `raw_schema`."""
    if not isinstance(raw_schema, dict):
        return ()
    bundle = raw_schema.get(BUNDLE_STORAGE_KEY)
    bundle_map: JsonSchemaObject = bundle if isinstance(bundle, dict) else {}
    descriptors: list[MutationTargetDescriptor] = []

    def walk(node: object, recipe: tuple[WalkStep, ...], stack: tuple[int, ...]) -> None:
        if not isinstance(node, dict):
            return
        if id(node) in stack or len(stack) > MAX_WALK_DEPTH:
            return
        new_stack = stack + (id(node),)

        # Bundled $ref: dereference and descend into the target. Unreachable targets (missing or cyclic) yield
        # no descriptor for the inner walk. Fall through afterwards: OpenAPI 3.1 / JSON Schema 2019-09+ allow
        # `$ref` to have sibling keywords (e.g. `{$ref, minLength: 3}`), so the wrapper itself can carry
        # mutable constraints.
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(REFERENCE_TO_BUNDLE_PREFIX):
            target_name = ref.rsplit("/", 1)[-1]
            target = bundle_map.get(target_name)
            if isinstance(target, dict) and id(target) not in new_stack:
                walk(target, recipe + (WalkStep("$ref", target_name),), new_stack)

        # Skip nodes with no mutable content (empty `{}`, external `$ref`-only wrappers). Operators have
        # nothing to negate at such targets; emitting a descriptor would waste a primary slot on
        # guaranteed-FAILURE dispatch.
        if any(key != BUNDLE_STORAGE_KEY and key != "$ref" for key in node):
            descriptors.append(MutationTargetDescriptor(walk=recipe))

        properties = node.get("properties")
        if isinstance(properties, dict):
            for name, sub in properties.items():
                walk(sub, recipe + (WalkStep("properties", name),), new_stack)
        items = node.get("items")
        if isinstance(items, dict):
            walk(items, recipe + (WalkStep("items", None),), new_stack)
        elif isinstance(items, list):
            for index, item in enumerate(items):
                walk(item, recipe + (WalkStep("items", index),), new_stack)
        additional = node.get("additionalProperties")
        if isinstance(additional, dict):
            walk(additional, recipe + (WalkStep("additionalProperties", None),), new_stack)
        pattern_props = node.get("patternProperties")
        if isinstance(pattern_props, dict):
            for pattern, sub in pattern_props.items():
                walk(sub, recipe + (WalkStep("patternProperties", pattern),), new_stack)
        for keyword in ("oneOf", "anyOf", "allOf"):
            branches = node.get(keyword)
            if isinstance(branches, list):
                for index, branch in enumerate(branches):
                    walk(branch, recipe + (WalkStep(keyword, index),), new_stack)

    walk(raw_schema, (), ())
    return tuple(descriptors)


def _descend(node: JsonSchemaObject, keyword: PathKeyword, selector: PathSelector) -> JsonSchemaObject:
    """Navigate one structural keyword from `node`. Caller resolves $refs separately."""
    match keyword:
        case "properties":
            return node["properties"][selector]
        case "items":
            return node["items"] if selector is None else node["items"][selector]
        case "oneOf" | "anyOf" | "allOf":
            return node[keyword][selector]
        case "additionalProperties":
            return node["additionalProperties"]
        case "patternProperties":
            return node["patternProperties"][selector]


def _materialize_one(
    new_schema: JsonSchemaObject,
    descriptor: MutationTargetDescriptor,
    bundle_map: JsonSchemaObject,
) -> MutationTarget | None:
    """Replay one descriptor against `new_schema` and return the resolved MutationTarget.

    Returns `None` when the walk can't be followed (e.g., a `properties` hop where
    the parent has no `properties` key, or a `$ref` hop into a missing bundle entry).
    This happens when error-feedback adjustments transform the schema between
    strategy build and case generation.
    """
    steps: list[PathStep] = []
    current: JsonSchemaObject = new_schema
    for hop in descriptor.walk:
        if hop.keyword == "$ref":
            assert isinstance(hop.selector, str)
            target = bundle_map.get(hop.selector) if isinstance(bundle_map, dict) else None
            if not isinstance(target, dict):
                return None
            current = target
            continue
        try:
            next_node = _descend(current, hop.keyword, hop.selector)
        except (KeyError, IndexError, TypeError):
            return None
        steps.append(PathStep(parent=current, keyword=hop.keyword, selector=hop.selector))
        current = next_node
    # The walk explicitly steps through every `$ref` via a `WalkStep("$ref", …)` hop, so a node ending the
    # walk on `$ref` is intentional — sibling-bearing wrappers (`{$ref, minLength: 3}`) need their siblings
    # mutated, not the dereferenced target. Don't defensively dereference here.
    return MutationTarget(schema=current, path=tuple(steps))


def _materialize_targets(
    new_schema: JsonSchemaObject, descriptors: tuple[MutationTargetDescriptor, ...]
) -> list[MutationTarget]:
    """Replay every descriptor against `new_schema` and return the resolved targets."""
    bundle = new_schema.get(BUNDLE_STORAGE_KEY)
    bundle_map = bundle if isinstance(bundle, dict) else {}
    targets: list[MutationTarget] = []
    for descriptor in descriptors:
        target = _materialize_one(new_schema, descriptor, bundle_map)
        if target is not None:
            targets.append(target)
    return targets


def _propagate_required_path(path: tuple[PathStep, ...]) -> None:
    """Force `required` (or equivalent) at each ancestor along `path`, mutating parents in place."""
    for step in path:
        selector = step.selector
        parent = step.parent
        match step.keyword:
            case "properties":
                assert isinstance(selector, str)
                required = parent.setdefault("required", [])
                if selector not in required:
                    required.append(selector)
            case "items":
                assert selector is None or isinstance(selector, int)
                min_required = 1 if selector is None else selector + 1
                if parent.get("minItems", 0) < min_required:
                    parent["minItems"] = min_required
            case "oneOf" | "anyOf" as keyword:
                assert isinstance(selector, int)
                branches = parent.get(keyword)
                if not isinstance(branches, list):
                    continue
                # Earlier sibling propagation may already have collapsed this list to one branch.
                if selector < len(branches):
                    parent[keyword] = [branches[selector]]
            case "allOf":
                pass
            case "additionalProperties":
                additional = parent.get("additionalProperties")
                if additional is None:
                    continue
                synthesized = _synthesize_property_name(parent)
                parent.setdefault("properties", {})[synthesized] = additional
                required = parent.setdefault("required", [])
                if synthesized not in required:
                    required.append(synthesized)
            case "patternProperties":
                assert isinstance(selector, str)
                pattern_map = parent.get("patternProperties")
                if not isinstance(pattern_map, dict) or selector not in pattern_map:
                    continue
                synthesized_pattern = _synthesize_pattern_property_name(selector)
                if synthesized_pattern is None:
                    continue
                parent.setdefault("properties", {})[synthesized_pattern] = pattern_map[selector]
                required = parent.setdefault("required", [])
                if synthesized_pattern not in required:
                    required.append(synthesized_pattern)


def _disjoint_descriptor_pool(
    candidates: tuple[MutationTargetDescriptor, ...], chosen: list[MutationTargetDescriptor]
) -> list[MutationTargetDescriptor]:
    """Filter descriptors that don't conflict with any already-chosen one.

    Two walks conflict when one is a prefix of the other (chain), or when they
    diverge at a `oneOf`/`anyOf` step (sibling branches; the schema mutation at
    the chosen branch collapses the alternative). Used by the dispatcher to
    drop disqualified candidates before materialization.
    """

    def is_on_chain(a: tuple[WalkStep, ...], b: tuple[WalkStep, ...]) -> bool:
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        return longer[: len(shorter)] == shorter

    def shares_oneof_sibling(a: tuple[WalkStep, ...], b: tuple[WalkStep, ...]) -> bool:
        for step_a, step_b in zip(a, b, strict=False):
            if step_a == step_b:
                continue
            if step_a.keyword in ("oneOf", "anyOf") and step_a.keyword == step_b.keyword:
                return True
            return False
        return False

    def keep(candidate: MutationTargetDescriptor) -> bool:
        for picked in chosen:
            if is_on_chain(candidate.walk, picked.walk):
                return False
            if shares_oneof_sibling(candidate.walk, picked.walk):
                return False
        return True

    return [candidate for candidate in candidates if keep(candidate)]


def _absolutize(target: MutationTarget, local: Mutation) -> Mutation:
    """Prefix a local Mutation's path/schema_pointer with the target's path-from-root.

    Also derives the parameter name from the path's last `properties` step when the
    operator didn't set one explicitly. Operators only set `parameter` when negating
    a single-element `required` list; for any other mutation on a parameter-shaped
    target (a single property under root), we want the parameter name to surface
    in error messages so callers see "parameter `X` in <location>" instead of just
    "in <location>".
    """
    body_path_prefix: ParameterPath = tuple(
        step.selector for step in target.path if step.keyword == "properties" and isinstance(step.selector, str)
    )
    pointer_segments: list[str] = []
    for step in target.path:
        pointer_segments.append(step.keyword)
        if step.selector is not None:
            pointer_segments.append(str(step.selector))
    schema_pointer_prefix = "/" + "/".join(pointer_segments) if pointer_segments else ""
    parameter = local.parameter
    if parameter is None:
        for step in reversed(target.path):
            if step.keyword == "properties" and isinstance(step.selector, str):
                parameter = step.selector
                break
    return Mutation(
        path=body_path_prefix + local.path,
        schema_pointer=schema_pointer_prefix + local.schema_pointer,
        channel=local.channel,
        operator=local.operator,
        keywords=local.keywords,
        parameter=parameter,
        original_value=local.original_value,
        new_value=local.new_value,
    )


def _synthesize_property_name(parent: JsonSchemaObject) -> str:
    """Return a property name not already in `parent.properties`."""
    properties = parent.get("properties", {})
    if "k" not in properties:
        return "k"
    counter = 0
    while f"k{counter}" in properties:
        counter += 1
    return f"k{counter}"


# Curated probe set covering common JSON-Schema patternProperties shapes: lowercase / uppercase /
# alphanumeric / kebab- / snake- / CamelCase / digit. Tried in order, so the simplest matching name wins.
_PATTERN_NAME_CANDIDATES = ("x", "X", "0", "x0", "Xx", "_x", "x-y", "x_y", "foo", "Foo")


def _synthesize_pattern_property_name(pattern: str) -> str | None:
    """Return a literal property name matching `pattern`, or `None` if no candidate satisfies it."""
    try:
        regex = re.compile(pattern)
    except re.error:
        return None
    for candidate in _PATTERN_NAME_CANDIDATES:
        if regex.search(candidate) is not None:
            return candidate
    return None


def metadata_with_description_override(
    *,
    operator: OperatorKind,
    parameter: str | None,
    description: str | None,
    location: str | None,
    keywords: tuple[str, ...] = (),
) -> MutationMetadata:
    """Build a MutationMetadata whose description is supplied directly.

    Used by the syntax-fuzzing path: the random-bytes payload has no structured
    keyword/path to attribute the violation to, so the producer hands in a
    pre-formatted message ("Invalid syntax: random bytes") instead of having
    `description` derive one from a Mutation record.
    """
    return MutationMetadata(
        mutations=(
            Mutation(
                path=(),
                schema_pointer=location or "",
                channel=MutationChannel.SCHEMA,
                operator=operator,
                keywords=keywords,
                parameter=parameter,
                original_value=None,
                new_value=None,
            ),
        ),
        description=description,
    )


class MutationResult(int, enum.Enum):
    """The result of applying some mutation to some schema.

    Failing to mutate something means that by applying some mutation, it is not possible to change
    the schema in the way, so it covers inputs not covered by the "positive" strategy.

    Knowing this, we know when the schema is mutated and whether we need to apply more mutations.
    """

    SUCCESS = 1
    FAILURE = 2

    def __ior__(self, other: Any) -> MutationResult:
        return self | other

    def __or__(self, other: Any) -> MutationResult:
        # Syntactic sugar to simplify handling of multiple results
        if self == MutationResult.SUCCESS:
            return self
        return other


# Mutator contract:
#   - SUCCESS  -> (MutationResult.SUCCESS, MutationMetadata(mutations=(one_mutation,)))
#   - FAILURE  -> (MutationResult.FAILURE, None)
# The dispatcher relies on len(mutations) == 1 for any successful operator return;
# operators must not produce multi-mutation metadata.
Mutator: TypeAlias = Callable[["MutationContext", Draw, Schema], tuple[MutationResult, MutationMetadata | None]]
ANY_TYPE_KEYS = {"$ref", "allOf", "anyOf", "const", "else", "enum", "if", "not", "oneOf", "then", "type"}
TYPE_SPECIFIC_KEYS = {
    "number": ("multipleOf", "maximum", "exclusiveMaximum", "minimum", "exclusiveMinimum"),
    "integer": ("multipleOf", "maximum", "exclusiveMaximum", "minimum", "exclusiveMinimum"),
    "string": ("maxLength", "minLength", "pattern", "format", "contentEncoding", "contentMediaType"),
    "array": ("items", "additionalItems", "maxItems", "minItems", "uniqueItems", "contains"),
    "object": (
        "maxProperties",
        "minProperties",
        "required",
        "properties",
        "patternProperties",
        "additionalProperties",
        "dependencies",
        "propertyNames",
    ),
}


class MutationContext:
    """Meta information about the current mutation state."""

    __slots__ = (
        "keywords",
        "non_keywords",
        "location",
        "media_type",
        "allow_extra_parameters",
        "name_to_uri",
        "target_descriptors",
    )

    # Validation keywords only.
    keywords: Schema
    # Everything else (extensions, x-bundled, etc.).
    non_keywords: Schema
    location: ParameterLocation
    media_type: str | None
    allow_extra_parameters: bool
    # Bundled name -> original URI, for error display.
    name_to_uri: dict[str, str]
    target_descriptors: tuple[MutationTargetDescriptor, ...]

    def __init__(
        self,
        *,
        keywords: Schema,
        non_keywords: Schema,
        location: ParameterLocation,
        media_type: str | None,
        allow_extra_parameters: bool,
        name_to_uri: dict[str, str] | None = None,
        target_descriptors: tuple[MutationTargetDescriptor, ...],
    ) -> None:
        self.keywords = keywords
        self.non_keywords = non_keywords
        self.location = location
        self.media_type = media_type
        self.allow_extra_parameters = allow_extra_parameters
        self.name_to_uri = name_to_uri or {}
        self.target_descriptors = target_descriptors

    @property
    def is_path_location(self) -> bool:
        return self.location == ParameterLocation.PATH

    @property
    def is_query_location(self) -> bool:
        return self.location == ParameterLocation.QUERY

    def ensure_bundle(self, schema: Schema) -> None:
        """Ensure schema has the bundle from context if needed.

        This is necessary when working with nested schemas (e.g., property schemas)
        that may contain bundled references but don't have the x-bundled key themselves.
        """
        # NOTE: nested targets get the *original* bundle reference, while `mutate()` clones the
        # bundle for the root schema. Operators today only read bundle entries, so the aliasing is
        # latent — but if any future operator writes into a bundle entry on a nested target, the
        # mutation will leak across cases. Clone here (or thread `bundle_map`) before that happens.
        if BUNDLE_STORAGE_KEY in self.non_keywords and BUNDLE_STORAGE_KEY not in schema:
            schema[BUNDLE_STORAGE_KEY] = self.non_keywords[BUNDLE_STORAGE_KEY]

    def mutate(self, draw: Draw) -> tuple[Schema, MutationMetadata | None]:
        """Target-dispatch: pick one primary target uniformly + up to 2 disjoint secondaries.

        One operator runs per chosen target; `required` is propagated along each path.
        """
        new_schema = deepclone(self.keywords)
        if BUNDLE_STORAGE_KEY in self.non_keywords:
            new_schema[BUNDLE_STORAGE_KEY] = deepclone(self.non_keywords[BUNDLE_STORAGE_KEY])

        descriptors = self.target_descriptors
        if not descriptors:
            reject()
        bundle = new_schema.get(BUNDLE_STORAGE_KEY)
        bundle_map = bundle if isinstance(bundle, dict) else {}

        random_state = draw(st.randoms())
        # Operator-swarm mask: each case sees a random subset of operators
        # (Groce et al., "Swarm Testing", ISSTA '12, DOI 10.1145/2338965.2336763).
        enabled_operators = draw(st.shared(FeatureStrategy(), key="operators"))

        # Uniform sampling over a precomputed list of every reachable site counters the root-bias diagnosed
        # in Regehr, "Helping Generative Fuzzers Avoid Looking Only Where the Light is Good"
        # (blog.regehr.org/archives/1700, 2019): without it, deeper leaves draw exponentially less weight than the root
        primary_descriptor = draw(st.sampled_from(descriptors)) if len(descriptors) > 1 else descriptors[0]
        primary = _materialize_one(new_schema, primary_descriptor, bundle_map)
        if primary is None:
            reject()
        assert primary is not None
        primary_operators = self._applicable_operators(primary)
        if not primary_operators:
            reject()

        chosen_descriptors: list[MutationTargetDescriptor] = [primary_descriptor]
        chosen: list[tuple[MutationTarget, list[Mutator]]] = [(primary, primary_operators)]

        for _ in range(MAX_SECONDARY_TARGETS):
            if random_state.random() >= 0.3:
                continue
            pool = _disjoint_descriptor_pool(descriptors, chosen_descriptors)
            if not pool:
                break
            secondary_descriptor = draw(st.sampled_from(pool)) if len(pool) > 1 else pool[0]
            secondary = _materialize_one(new_schema, secondary_descriptor, bundle_map)
            if secondary is None:
                continue
            secondary_operators = self._applicable_operators(secondary)
            if not secondary_operators:
                continue
            chosen_descriptors.append(secondary_descriptor)
            chosen.append((secondary, secondary_operators))

        mutations: list[Mutation] = []
        for target, applicable in chosen:
            mutation = self._apply_one_at_target(draw, target, applicable, enabled_operators)
            if mutation is None:
                continue
            mutations.append(_absolutize(target, mutation))
            _propagate_required_path(target.path)

        if not mutations:
            reject()

        # Non-keyword fields pass through; keep the already-cloned bundle if operators mutated targets inside it.
        for key, value in self.non_keywords.items():
            if key == BUNDLE_STORAGE_KEY and key in new_schema:
                continue
            new_schema[key] = value
        if self.location.is_in_header:
            new_schema["propertyNames"] = {"type": "string", "format": "_header_name"}
            for sub_schema in new_schema.get("properties", {}).values():
                sub_schema["type"] = "string"
                # `_header_value` keeps generated values within RFC 9110 codepoints so `is_valid_header` doesn't
                # reject them. Without this, mutated header sub-schemas (e.g. `{type: string, not: {pattern: …}}`)
                # produce non-Latin-1 strings that the upstream filter discards, forcing Hypothesis to fall back
                # to empty values that hide bugs.
                sub_schema.setdefault("format", "_header_value")
            if self.allow_extra_parameters and draw(st.booleans()):
                # Headers default `additionalProperties: False`, so without this branch Schemathesis can never generate
                # undeclared header names — opt in here to exercise that surface when extra parameters are allowed.
                new_schema["additionalProperties"] = {"type": "string", "format": "_header_value"}
        # Empty arrays / objects may still validate against the original schema,
        # turning a "negative" case into a positive one — force at least one element.
        if "array" in get_type(new_schema) and new_schema.get("items") and "minItems" not in new_schema.get("not", {}):
            new_schema.setdefault("minItems", 1)
        if (
            "object" in get_type(new_schema)
            and new_schema.get("properties")
            and "minProperties" not in new_schema.get("not", {})
        ):
            new_schema.setdefault("minProperties", 1)

        return new_schema, MutationMetadata(mutations=tuple(mutations))

    def _apply_one_at_target(
        self, draw: Draw, target: MutationTarget, applicable: list[Mutator], enabled_operators: FeatureFlags
    ) -> Mutation | None:
        """Pick one operator from the swarm-masked applicable set; apply at target.schema."""
        self.ensure_bundle(target.schema)
        masked = [operator for operator in applicable if enabled_operators.is_enabled(operator.__name__)]
        candidates = masked or applicable
        for operator in draw(ordered(candidates, unique_by=lambda fn: fn.__name__)):
            result, metadata = operator(self, draw, target.schema)
            if result == MutationResult.SUCCESS and metadata is not None and metadata.mutations:
                return metadata.mutations[0]
        return None

    def _applicable_operators(self, target: MutationTarget) -> list[Mutator]:
        """Per-location applicability table.

        BODY accepts every operator at every target. HEADER/COOKIE/QUERY/PATH
        preserve the root parameter object (negation only — never type-change or
        required-removal that would strip declared params); deeper per-parameter
        schemas additionally accept change_type.
        """
        is_root = len(target.path) == 0
        if self.location == ParameterLocation.BODY:
            return [negate_constraints, change_type, remove_required_property]
        if self.location.is_in_header or self.location == ParameterLocation.QUERY:
            return [negate_constraints] if is_root else [negate_constraints, change_type]
        if self.location == ParameterLocation.PATH:
            return [] if is_root else [negate_constraints, change_type]
        return []


def for_types(*allowed_types: str) -> Callable[[Mutator], Mutator]:
    """Immediately return FAILURE for schemas with types not from ``allowed_types``."""
    _allowed_types = set(allowed_types)

    def wrapper(mutation: Mutator) -> Mutator:
        @wraps(mutation)
        def inner(ctx: MutationContext, draw: Draw, schema: Schema) -> tuple[MutationResult, MutationMetadata | None]:
            types = get_type(schema)
            if _allowed_types & set(types):
                return mutation(ctx, draw, schema)
            return MutationResult.FAILURE, None

        return inner

    return wrapper


@for_types("object")
def remove_required_property(
    ctx: MutationContext, draw: Draw, schema: Schema
) -> tuple[MutationResult, MutationMetadata | None]:
    """Remove a required property.

    Effect: Some property won't be generated.
    """
    required = schema.get("required")
    if not required:
        return MutationResult.FAILURE, None
    if len(required) == 1:
        property_name = draw(st.sampled_from(sorted(required)))
    else:
        candidate = draw(st.sampled_from(sorted(required)))
        enabled_properties = draw(st.shared(FeatureStrategy(), key="properties"))
        candidates = [candidate, *sorted([prop for prop in required if enabled_properties.is_enabled(prop)])]
        property_name = draw(st.sampled_from(candidates))
    required.remove(property_name)
    if not required:
        # Draft 4 requires `required` to be non-empty when present.
        del schema["required"]
    # Drop the property too; an optional property would still be generatable.
    properties = schema.get("properties", {})
    properties.pop(property_name, None)
    if properties == {}:
        schema.pop("properties", None)
    schema["type"] = "object"
    # `patternProperties` can still produce this name; the output filter catches that case.
    mutation = Mutation(
        path=(),
        schema_pointer=f"/properties/{property_name}",
        channel=MutationChannel.SCHEMA,
        operator=OperatorKind.REMOVE_REQUIRED_PROPERTY,
        keywords=("required",),
        parameter=property_name,
        original_value=None,
        new_value=None,
    )
    return MutationResult.SUCCESS, MutationMetadata(mutations=(mutation,))


def change_type(
    ctx: MutationContext, draw: Draw, schema: JsonSchemaObject
) -> tuple[MutationResult, MutationMetadata | None]:
    """Change type of values accepted by a schema."""
    if "type" not in schema:
        # No `type`: schema accepts every type; nothing to change.
        return MutationResult.FAILURE, None
    if ctx.media_type == "application/x-www-form-urlencoded":
        # Form data must stay object-shaped.
        return MutationResult.FAILURE, None
    old_types = get_type(schema)
    # `string` is wire-equivalent to "anything" for string-stringifying transports — path/query/header/cookie
    # always serialize to strings, XML bodies stringify via `_escape_xml`, binary/byte bodies accept any bytes.
    # Skip rather than emit a mutation that won't change what reaches the server.
    if "string" in old_types and (
        ctx.location.is_in_header
        or ctx.is_path_location
        or ctx.is_query_location
        or (
            ctx.location == ParameterLocation.BODY
            and (is_binary_format(schema) or (ctx.media_type is not None and is_xml(ctx.media_type)))
        )
    ):
        return MutationResult.FAILURE, None
    candidates = _get_type_candidates_with_weights(ctx, schema, draw)
    if not candidates:
        return MutationResult.FAILURE, None
    if len(candidates) == 1:
        new_type = candidates.pop()
        schema["type"] = new_type
        _ensure_query_serializes_to_non_empty(ctx, schema)
        _ensure_path_string_not_numeric(ctx, schema, old_types)
        _ensure_boolean_not_coercible(ctx, schema, old_types)
        prevent_unsatisfiable_schema(schema, new_type)
    else:
        candidate = draw(st.sampled_from(sorted(candidates)))
        candidates.remove(candidate)
        enabled_types = draw(st.shared(FeatureStrategy(), key="types"))
        remaining_candidates = [
            candidate,
            *sorted([candidate for candidate in candidates if enabled_types.is_enabled(candidate)]),
        ]
        new_type = draw(st.sampled_from(remaining_candidates))
        schema["type"] = new_type
        _ensure_query_serializes_to_non_empty(ctx, schema)
        _ensure_path_string_not_numeric(ctx, schema, old_types)
        _ensure_boolean_not_coercible(ctx, schema, old_types)
        prevent_unsatisfiable_schema(schema, new_type)

    mutation = Mutation(
        path=(),
        schema_pointer="",
        channel=MutationChannel.SCHEMA,
        operator=OperatorKind.CHANGE_TYPE,
        keywords=("type",),
        parameter=None,
        original_value=" | ".join(sorted(old_types)) if len(old_types) > 1 else old_types[0],
        new_value=new_type,
    )
    return MutationResult.SUCCESS, MutationMetadata(mutations=(mutation,))


def _ensure_query_serializes_to_non_empty(ctx: MutationContext, schema: Schema) -> None:
    if ctx.is_query_location and schema.get("type") == "array":
        # Empty arrays / `None` items / empty objects all serialize to a missing query string, which the request
        # would never carry — force at least one value-bearing element so the mutation actually reaches the server.
        schema["minItems"] = schema.get("minItems") or 1
        schema.setdefault("items", {}).update({"not": {"enum": [None, [], {}]}})


def _ensure_path_string_not_numeric(ctx: MutationContext, schema: Schema, old_types: list[str]) -> None:
    """Exclude numeric strings when mutating integer/number to string for path parameters.

    Numeric strings like "7" serialize to the same URL as integer 7,
    making them indistinguishable and causing false positive failures.
    """
    if not ctx.is_path_location:
        return
    if schema.get("type") != "string":
        return
    if "integer" not in old_types and "number" not in old_types:
        return
    schema["not"] = {"pattern": r"^-?\d+\.?\d*$"}


def _ensure_boolean_not_coercible(ctx: MutationContext, schema: Schema, old_types: list[str]) -> None:
    """Exclude wire-coercible values when mutating a boolean query/path parameter.

    Lenient parsers read 0/1/true/false as booleans, so those serialize to a value the server
    accepts as valid, making the mutation indistinguishable from the original boolean.
    """
    if not (ctx.is_query_location or ctx.is_path_location):
        return
    if "boolean" not in old_types:
        return
    new_type = schema.get("type")
    if new_type in ("integer", "number"):
        schema["not"] = {"enum": [0, 1]}
    elif new_type == "string":
        schema["not"] = {"pattern": r"(?i)^(?:true|false|0|1)$"}


def _get_type_candidates(ctx: MutationContext, schema: Schema) -> set[str]:
    types = set(get_type(schema))
    if ctx.is_path_location:
        # Path params: skip null/boolean by default — they rarely surface real
        # bugs and waste budget; `_get_type_candidates_with_weights` adds them back occasionally.
        candidates = {"string", "integer", "number"} - types
    else:
        candidates = {"string", "integer", "number", "object", "array", "boolean", "null"} - types
    # A single-element array (`[0]` -> `flag=0`) or single-key object (`{"0": ...}` -> `flag=0`)
    # serializes to a scalar query value that can still read as a coercible boolean — drop both.
    if "boolean" in types and ctx.is_query_location:
        candidates.discard("array")
        candidates.discard("object")
    # Every integer is a number and vice versa from the validator's perspective —
    # neither swap produces values the original schema rejects.
    if "integer" in types and "number" in candidates:
        candidates.remove("number")
    if "number" in types and "integer" in candidates:
        candidates.remove("integer")
    return candidates


# Per-case probability for surfacing null/boolean as path-parameter mutations.
PATH_NULL_BOOLEAN_PROBABILITY = 0.05


def _get_type_candidates_with_weights(ctx: MutationContext, schema: Schema, draw: Draw) -> set[str]:
    """Path-aware candidate set: re-introduces null/boolean at low probability."""
    candidates = _get_type_candidates(ctx, schema)
    if ctx.is_path_location:
        types = set(get_type(schema))
        random = draw(st.randoms())
        if "null" not in types and random.random() < PATH_NULL_BOOLEAN_PROBABILITY:
            candidates.add("null")
        if "boolean" not in types and random.random() < PATH_NULL_BOOLEAN_PROBABILITY:
            candidates.add("boolean")
    return candidates


def prevent_unsatisfiable_schema(schema: Schema, new_type: str) -> None:
    """Drop keywords that would conflict with `new_type` in the schema and its `not` branch."""
    drop_not_type_specific_keywords(schema, new_type)
    if "not" in schema:
        drop_not_type_specific_keywords(schema["not"], new_type)
        if not schema["not"]:
            del schema["not"]


def drop_not_type_specific_keywords(schema: Schema, new_type: str) -> None:
    """Remove keywords that are not applicable to the defined type."""
    keywords = TYPE_SPECIFIC_KEYS.get(new_type, ())
    for keyword in tuple(schema):
        if keyword not in keywords and keyword not in ANY_TYPE_KEYS:
            schema.pop(keyword, None)


def negate_constraints(
    ctx: MutationContext, draw: Draw, schema: Schema
) -> tuple[MutationResult, MutationMetadata | None]:
    """Negate schema constrains while keeping the original type."""
    ctx.ensure_bundle(schema)
    if not can_negate(schema):
        return MutationResult.FAILURE, None
    copied = schema.copy()
    # Preserve x-bundled before clearing
    bundled = schema.get(BUNDLE_STORAGE_KEY)
    schema.clear()
    if bundled is not None:
        schema[BUNDLE_STORAGE_KEY] = bundled
    is_negated = False
    negated_keys = []

    def is_mutation_candidate(k: str, v: Any) -> bool:
        if k == "required":
            return v != []
        if k in ("example", "examples", BUNDLE_STORAGE_KEY):
            return False
        if ctx.is_path_location and k == "minLength" and v == 1:
            # Negating `minLength: 1` produces empty paths that the transport drops anyway.
            return False
        if (
            not ctx.allow_extra_parameters
            and k == "additionalProperties"
            and ctx.location in (ParameterLocation.QUERY, ParameterLocation.HEADER, ParameterLocation.COOKIE)
        ):
            return False
        return not (
            k in ("type", "properties", "items", "minItems")
            or (k == "additionalProperties" and ctx.location.is_in_header)
        )

    enabled_keywords = draw(st.shared(FeatureStrategy(), key="keywords"))
    candidates = []
    mutation_candidates = [key for key, value in copied.items() if is_mutation_candidate(key, value)]
    if mutation_candidates:
        # Pin one keyword as required-to-negate so the case isn't all-pass at low feature mask.
        candidate = draw(st.sampled_from([key for key, value in copied.items() if is_mutation_candidate(key, value)]))
        candidates.append(candidate)
        if candidate in DEPENDENCIES:
            candidates.append(DEPENDENCIES[candidate])
    for key, value in copied.items():
        if is_mutation_candidate(key, value):
            if key in candidates or enabled_keywords.is_enabled(key):
                is_negated = True
                negated_keys.append(key)
                # `format` is dropped rather than wrapped in `not:` — hypothesis-jsonschema treats format as
                # annotation-only, so removing it lets us generate values that won't match without validator help.
                if key != "format":
                    negated = schema.setdefault("not", {})
                    negated[key] = value
                    if key in DEPENDENCIES:
                        dependency = DEPENDENCIES[key]
                        if dependency not in negated and dependency in copied:
                            negated[dependency] = copied[dependency]
        else:
            schema[key] = value
    if is_negated:
        parameter = None
        original_required: list[JsonValue] | None = None
        for key in negated_keys:
            value = copied[key]
            if key == "required":
                # Carry the full list so `transport.prepare.get_exclude_headers` can
                # diff it against the actually-sent headers when `parameter` stays None.
                original_required = list(value)
                if len(value) == 1:
                    parameter = value[0]
                break
        mutation = Mutation(
            path=(),
            schema_pointer="",
            channel=MutationChannel.SCHEMA,
            operator=OperatorKind.NEGATE_CONSTRAINTS,
            keywords=tuple(negated_keys),
            parameter=parameter,
            original_value=original_required,
            new_value=None,
        )
        return MutationResult.SUCCESS, MutationMetadata(mutations=(mutation,))
    return MutationResult.FAILURE, None


DEPENDENCIES = {"exclusiveMaximum": "maximum", "exclusiveMinimum": "minimum"}


def ident(x: T) -> T:
    return x


def ordered(items: Sequence[T], unique_by: Callable[[T], Any] = ident) -> st.SearchStrategy[list[T]]:
    """Returns a strategy that generates randomly ordered lists of T.

    NOTE. Items should be unique.
    """
    return st.lists(st.sampled_from(items), min_size=len(items), unique_by=unique_by)
