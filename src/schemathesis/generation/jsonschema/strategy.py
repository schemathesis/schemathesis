from __future__ import annotations

import math
import re
from functools import reduce
from typing import TYPE_CHECKING, cast

import jsonschema_rs
from hypothesis import strategies as st

from schemathesis.core.jsonschema import FANCY_REGEX_OPTIONS

if TYPE_CHECKING:
    from collections.abc import Callable

    from hypothesis.strategies import SearchStrategy

    from schemathesis.core.jsonschema.types import JsonSchema, JsonValue
    from schemathesis.generation.jsonschema.context import StrategyContext

    _View = jsonschema_rs.canonical.CanonicalViewType
    _Lifter = Callable[..., SearchStrategy[JsonValue]]

_OPEN_ARRAY_MAX = 5
# Upper bound on spontaneously-generated additional properties when the count is otherwise open.
_OPEN_OBJECT_MAX = 5
# Hypothesis rejects `st.lists(min_size=...)` above its buffer cap (8192).
_LIST_CAP = 8192


def _compiles(pattern: str) -> bool:
    try:
        re.compile(pattern)
        return True
    except re.error:
        return False


def _validator_for(schema: JsonSchema) -> jsonschema_rs.Validator:
    # Use fancy-regex so large/lookaround `pattern`s don't trip the default engine's size limit.
    return jsonschema_rs.validator_for(schema, pattern_options=FANCY_REGEX_OPTIONS)


def _accepts(schema: JsonSchema) -> Callable[[JsonValue], bool]:
    validator = _validator_for(schema)

    def check(value: JsonValue) -> bool:
        try:
            return validator.is_valid(value)
        except ValueError:
            # Transport wrappers (e.g. binary payloads) aren't JSON-validatable but are valid by construction.
            return True

    return check


def from_schema(schema: jsonschema_rs.CanonicalSchema, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    if ctx.definitions is None:
        # The first schema is the root; its definitions() is the full transitive ref graph.
        ctx.definitions = schema.definitions()
    cached = ctx.cache.get(schema)
    if cached is not None:
        return cached
    if isinstance(schema.view(), jsonschema_rs.canonical.NotView):
        # `not` has its own cycle guard (`ctx.expanding`); a cache placeholder would short-circuit it.
        result = _build(schema, ctx)
        ctx.cache[schema] = result
        return result
    # Break content cycles from inlined self-referential schemas: a deferred placeholder lets a
    # re-entrant build of the same node return without recursing into its children again.
    holder: list[SearchStrategy[JsonValue]] = []
    ctx.cache[schema] = st.deferred(lambda: holder[0])
    result = _build(schema, ctx)
    holder.append(result)
    ctx.cache[schema] = result
    return result


def _build(schema: jsonschema_rs.CanonicalSchema, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    view = schema.view()
    if isinstance(view, jsonschema_rs.canonical.NotView):
        # Constructive negation; if the complement re-derives the same `not` (no positive form), filter.
        if schema in ctx.expanding:
            return _validated_against(schema, ctx)
        ctx.expanding.add(schema)
        try:
            return from_schema(view.schema.negate(), ctx)
        finally:
            ctx.expanding.discard(schema)
    if isinstance(view, jsonschema_rs.canonical.IfThenElseView):
        # No branch witness yet; draw any value and keep the valid ones.
        return _validated_against(schema, ctx)
    if isinstance(view, (jsonschema_rs.canonical.ReferenceView, jsonschema_rs.canonical.RecursiveView)):
        return _reference(view, ctx)
    if isinstance(view, jsonschema_rs.canonical.ObjectView):
        return _object(schema, view, ctx)
    if isinstance(view, jsonschema_rs.canonical.StringView):
        return _string(schema, view, ctx)
    lifter = _LIFTERS.get(type(view).__name__)
    if lifter is None:
        # DynamicRef / Raw: no IR-level lifter, so draw any value and keep the valid ones.
        return _validated_against(schema, ctx)
    return lifter(view, ctx)


def _reference(
    view: jsonschema_rs.canonical.ReferenceView | jsonschema_rs.canonical.RecursiveView, ctx: StrategyContext
) -> SearchStrategy[JsonValue]:
    uri = view.uri
    definitions = ctx.definitions
    if definitions is None or uri not in definitions:
        # Dangling / unresolvable ref: nothing satisfies it.
        return st.nothing()
    if uri not in ctx.references:
        # Lazy + shared: `st.deferred` ties recursive/mutual refs to one binding per uri.
        body = st.deferred(lambda: from_schema(definitions[uri], ctx))

        @st.composite  # type: ignore[untyped-decorator]
        def guarded(draw: st.DrawFn) -> JsonValue:
            # Cap recursion depth per uri so wide/deep cycles can't generate huge structures.
            depth = ctx.depths.get(uri, 0)
            if depth >= ctx.max_recursion_depth:
                return draw(st.nothing())
            ctx.depths[uri] = depth + 1
            try:
                return draw(body)
            finally:
                ctx.depths[uri] = depth

        ctx.references[uri] = guarded()
    return ctx.references[uri]


def _anything(ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    # Arbitrary JSON value; containers bounded to keep draws cheap.
    text = _text(ctx)
    return st.recursive(
        st.none()
        | st.booleans()
        | st.integers()
        | st.floats(allow_nan=False, allow_infinity=False).map(lambda x: x or 0.0)
        | text,
        lambda children: st.lists(children, max_size=3) | st.dictionaries(text, children, max_size=3),
    )


def _text(ctx: StrategyContext, **kwargs: int) -> SearchStrategy[str]:
    codec = ctx.alphabet.codec
    if codec is not None:
        alphabet = st.characters(codec=codec, exclude_characters="" if ctx.alphabet.allow_x00 else "\x00")
    else:
        alphabet = st.characters(exclude_characters="" if ctx.alphabet.allow_x00 else "\x00")
    return st.text(alphabet=alphabet, **kwargs)


def _true(view: object, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    return _anything(ctx)


def _false(view: object, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    return st.nothing()


def _const(view: jsonschema_rs.canonical.ConstView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    return st.just(cast("JsonValue", view.value))


def _enum(view: jsonschema_rs.canonical.EnumView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    return st.sampled_from(view.values)


def _integer(view: jsonschema_rs.canonical.IntegerView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    low, high = _int_bounds(view)
    multiple = view.multiple_of
    if multiple is None:
        base = st.integers(min_value=low, max_value=high)
    else:
        multiple = int(multiple)
        # `multipleOf` validation divides in f64; past 2**53 that is imprecise and rejects true multiples,
        # so bound the multiplier to the float-exact range when a side is open.
        safe = 2**53
        lo = -((-(low if low is not None else -safe)) // multiple)
        hi = (high if high is not None else safe) // multiple
        if lo > hi:
            # No in-range multiple we can generate (e.g. `multipleOf` exceeds the safe bound).
            return st.nothing()
        base = st.integers(min_value=lo, max_value=hi).map(lambda k: k * multiple)
    if view.not_multiple_of:
        excluded = [int(q) for q in view.not_multiple_of]
        base = base.filter(lambda v: all(q == 0 or v % q != 0 for q in excluded))
    return base


def _int_bounds(view: jsonschema_rs.canonical.IntegerView) -> tuple[int | None, int | None]:
    low = view.minimum
    if view.exclusive_minimum is not None:
        low = view.exclusive_minimum + 1 if low is None else max(low, view.exclusive_minimum + 1)
    high = view.maximum
    if view.exclusive_maximum is not None:
        high = view.exclusive_maximum - 1 if high is None else min(high, view.exclusive_maximum - 1)
    return (None if low is None else int(low), None if high is None else int(high))


def _number(view: jsonschema_rs.canonical.NumberView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    base = _number_base(view, ctx)
    if view.not_multiple_of:
        excluded = [float(q) for q in view.not_multiple_of]
        base = base.filter(lambda v: not any(_float_is_multiple(float(v), q) for q in excluded))
    return base


def _float_is_multiple(value: float, modulus: float) -> bool:
    if modulus == 0:
        return value == 0
    ratio = value / modulus
    return ratio == math.floor(ratio)


def _number_base(view: jsonschema_rs.canonical.NumberView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    low, exclude_min = view.minimum, False
    if view.exclusive_minimum is not None and (low is None or view.exclusive_minimum >= low):
        low, exclude_min = view.exclusive_minimum, True
    high, exclude_max = view.maximum, False
    if view.exclusive_maximum is not None and (high is None or view.exclusive_maximum <= high):
        high, exclude_max = view.exclusive_maximum, True
    multiple = view.multiple_of
    if multiple is not None:
        multiple = float(multiple)
        try:
            klo = None if low is None else math.ceil(float(low) / multiple)
            khi = None if high is None else math.floor(float(high) / multiple)
        except (OverflowError, ValueError):
            # Bound / `multipleOf` ratio overflows the float range; no witness we can build.
            return st.nothing()
        if klo is not None and khi is not None and klo > khi:
            return st.nothing()
        base = st.integers(min_value=klo, max_value=khi).map(lambda k: k * multiple)
        # Drop a grid multiple that lands exactly on an excluded bound.
        if exclude_min:
            base = base.filter(lambda v: v > low)
        if exclude_max:
            base = base.filter(lambda v: v < high)
        return base
    # Fold exclusivity into the bound (one `nextafter` step) so `st.floats` never has to,
    # which lets us detect the degenerate "stepped past the float range" case as empty.
    if low is not None:
        low = _representable_float(low, upper=False)
        if exclude_min:
            low = math.nextafter(low, math.inf)
    if high is not None:
        high = _representable_float(high, upper=True)
        if exclude_max:
            high = math.nextafter(high, -math.inf)
    if low == math.inf or high == -math.inf or (low is not None and high is not None and low > high):
        return st.nothing()
    return st.floats(
        min_value=None if low == -math.inf else low,
        max_value=None if high == math.inf else high,
        allow_nan=False,
        allow_infinity=False,
    )


def _representable_float(value: float, *, upper: bool) -> float:
    # `st.floats` requires exactly-representable bounds; nudge toward the valid side of `value`.
    as_float = float(value)
    if upper:
        return as_float if as_float <= value else math.nextafter(as_float, -math.inf)
    return as_float if as_float >= value else math.nextafter(as_float, math.inf)


def _conjunctive_pattern(patterns: list[str]) -> str | None:
    # Each pattern must have a search-match; lookaheads require all of them in one regex.
    if not patterns:
        return None
    if len(patterns) == 1:
        return patterns[0]
    return "".join(f"(?=[\\s\\S]*(?:{p}))" for p in patterns) + "[\\s\\S]*"


def _string(
    schema: jsonschema_rs.CanonicalSchema,
    view: jsonschema_rs.canonical.StringView,
    ctx: StrategyContext,
) -> SearchStrategy[JsonValue]:
    strategy = _string_base(view, ctx)
    # Exclude strings matching any compilable negated pattern.
    compilable_negated = [p for p in view.not_patterns if _compiles(p)]
    if compilable_negated:
        compiled = [re.compile(p) for p in compilable_negated]
        strategy = strategy.filter(lambda value: not any(rx.search(value) for rx in compiled))
    # Validate against the full schema for what construction can't guarantee: extra/uncompilable
    # patterns or an ECMA-only regex. `content` keywords are annotations, so `is_valid` skips them.
    dropped = any(not _compiles(p) for p in view.patterns) or len(compilable_negated) != len(view.not_patterns)
    if len(view.patterns) > 1 or dropped or view.extended_regex:
        strategy = strategy.filter(_accepts(schema.to_json_schema()))
    return strategy


def _string_base(view: jsonschema_rs.canonical.StringView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    registered = ctx.formats.get(view.format) if view.format is not None else None
    pattern = _conjunctive_pattern([p for p in view.patterns if _compiles(p)])
    if registered is not None or pattern is not None:
        if registered is not None:
            strategy = registered
            if pattern is not None:
                strategy = strategy.filter(re.compile(pattern).search)
        else:
            # `fullmatch` avoids `$` matching before a trailing newline (which the validator rejects);
            # full matches are a subset of the search matches the schema accepts, so it stays sound.
            strategy = st.from_regex(pattern, fullmatch=True)
        # Length is encoded into the pattern upstream (`update_quantifier`); filter is the soundness net.
        if view.min_length is not None or view.max_length is not None:
            low = view.min_length or 0
            high = math.inf if view.max_length is None else view.max_length
            strategy = strategy.filter(lambda value: low <= len(value) <= high)
        return strategy
    if view.min_length is not None and view.min_length > _LIST_CAP:
        # `st.text` rejects `min_size` above the buffer cap; pad with copies of one valid char.
        base = _text(ctx, max_size=_OPEN_ARRAY_MAX)
        filler = _text(ctx, min_size=1, max_size=1)
        return st.tuples(filler, base).map(lambda pair: pair[1] + pair[0] * (view.min_length - len(pair[1])))
    kwargs: dict[str, int] = {}
    if view.min_length is not None:
        kwargs["min_size"] = view.min_length
    if view.max_length is not None:
        kwargs["max_size"] = min(view.max_length, _LIST_CAP)
    return _text(ctx, **kwargs)


def _multi_type(view: jsonschema_rs.canonical.MultiTypeView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    return st.one_of([_bare_type(name, ctx) for name in view.types])


def _bare_type(name: str, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    if name == "null":
        return st.none()
    if name == "boolean":
        return st.booleans()
    if name == "integer":
        return st.integers()
    if name == "number":
        return st.floats(allow_nan=False, allow_infinity=False)
    if name == "string":
        return _text(ctx)
    if name == "array":
        return st.lists(_anything(ctx))
    return st.dictionaries(_text(ctx), _anything(ctx))


def _array(view: jsonschema_rs.canonical.ArrayView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    # `tail is None` means items past the prefix are unconstrained, not forbidden.
    # `maxItems` below the prefix length caps the array: only the first `max_items` prefix
    # positions can exist, so generate that many (a legacy tuple `items` with a small `maxItems`).
    prefix_schemas = list(view.prefix)
    if view.max_items is not None:
        prefix_schemas = prefix_schemas[: view.max_items]
    prefix = [from_schema(item, ctx) for item in prefix_schemas]
    extra_lo = max(0, view.min_items - len(prefix))
    if view.tail is not None and isinstance(view.tail.view(), jsonschema_rs.canonical.FalseView):
        # `items: false`: no elements past the prefix, so the array is exactly the prefix length.
        tail = st.nothing()
        extra_hi = extra_lo = 0
    else:
        tail = from_schema(view.tail, ctx) if view.tail is not None else _anything(ctx)
        # Unbounded `maxItems`: cap the generated length so open tails don't draw pathologically large lists.
        extra_hi = max(extra_lo, _OPEN_ARRAY_MAX) if view.max_items is None else max(0, view.max_items - len(prefix))
        if view.contains:
            # Bias items toward `contains` witnesses so a tight `minContains` is reachable; a witness
            # must also satisfy the tail, so intersect when the tail constrains items.
            witnesses = [
                from_schema(c.schema if view.tail is None else view.tail.intersect(c.schema), ctx)
                for c in view.contains
            ]
            tail = st.one_of([tail, *witnesses])
    if view.repeated_items:
        # `not uniqueItems`: the array must contain a duplicated pair. Force two tail items and make
        # one a copy of another (both share the `tail` schema, so the array stays valid).
        if view.max_items is not None and len(prefix) + 2 > view.max_items:
            return st.nothing()
        extra_lo = max(extra_lo, 2)
        extra_hi = max(extra_hi, extra_lo)
    unique_by = jsonschema_rs.canonical.json.to_string if view.unique_items else None
    if extra_lo > _LIST_CAP and not view.unique_items:
        # `st.lists` rejects `min_size` above Hypothesis's buffer cap; pad with copies of a valid item.
        base = st.lists(tail, max_size=_OPEN_ARRAY_MAX, unique_by=unique_by)
        body = st.tuples(tail, base).map(lambda pair: pair[1] + [pair[0]] * (extra_lo - len(pair[1])))
    else:
        body = st.lists(tail, min_size=min(extra_lo, _LIST_CAP), max_size=min(extra_hi, _LIST_CAP), unique_by=unique_by)
    if view.repeated_items:
        body = body.map(_force_duplicate)
    combined = st.tuples(st.tuples(*prefix).map(list), body).map(lambda parts: parts[0] + parts[1])
    if view.unique_items:
        combined = combined.filter(_all_unique)
    if view.repeated_items:
        combined = combined.filter(lambda xs: not _all_unique(xs))
    if view.contains:
        combined = combined.filter(_contains_check(view.contains))
    return combined


def _force_duplicate(values: list[JsonValue]) -> list[JsonValue]:
    # All items share the `tail` schema, so copying one over another keeps every element valid.
    if len(values) >= 2 and _all_unique(values):
        values = list(values)
        values[1] = values[0]
    return values


def _contains_check(
    contains: list[jsonschema_rs.canonical.ContainsView],
) -> Callable[[list[JsonValue]], bool]:
    checks = [(_accepts(c.schema.to_json_schema()), c.min_contains, c.max_contains) for c in contains]

    def ok(values: list[JsonValue]) -> bool:
        for accepts, low, high in checks:
            count = sum(1 for value in values if accepts(value))
            if count < low or (high is not None and count > high):
                return False
        return True

    return ok


def _all_unique(values: list[JsonValue]) -> bool:
    seen: list[str] = []
    for value in values:
        key = jsonschema_rs.canonical.json.to_string(value)
        if key in seen:
            return False
        seen.append(key)
    return True


def _object(
    schema: jsonschema_rs.CanonicalSchema, view: jsonschema_rs.canonical.ObjectView, ctx: StrategyContext
) -> SearchStrategy[JsonValue]:
    canon = jsonschema_rs.canonical
    # Child strategies are built once here, not per draw (the memo dedupes shared property shapes).
    required = [r.name for r in view.requirements if isinstance(r, canon.RequiredProperty)]
    dependent_required = [
        (r.property, list(r.required_properties))
        for r in view.requirements
        if isinstance(r, canon.DependentPropertiesRequirement)
    ]
    pattern_required = [r.pattern for r in view.requirements if isinstance(r, canon.PatternPropertyRequirement)]
    # Existential over the additional-name set: at least one property not covered by
    # `properties`/`patternProperties` must satisfy each schema.
    additional_required = [
        from_schema(r.schema, ctx) for r in view.requirements if isinstance(r, canon.AdditionalPropertiesRequirement)
    ]
    exact = {
        c.name: from_schema(c.schema, ctx) for c in view.constraints if isinstance(c, canon.NamedPropertyConstraint)
    }
    patterns = [
        (c.pattern, from_schema(c.schema, ctx))
        for c in view.constraints
        if isinstance(c, canon.PatternPropertyConstraint)
    ]
    # Patterns Python `re` can't compile (e.g. ECMA `\p{L}`) can't drive name generation/matching; drop
    # them here and validate the whole object below so name->value routing stays sound.
    usable_patterns = [(pattern, value) for pattern, value in patterns if _compiles(pattern)]
    usable_pattern_required = [pattern for pattern in pattern_required if _compiles(pattern)]
    needs_validation = len(usable_patterns) != len(patterns) or len(usable_pattern_required) != len(pattern_required)
    # Injected additional names are routed approximately (propertyNames / pattern overlap); validate as the net.
    needs_validation = needs_validation or bool(additional_required)
    patterns = usable_patterns
    pattern_required = usable_pattern_required
    additional = _additional_value(view, ctx)
    for name in required:
        exact.setdefault(name, _anything(ctx))
    optional = [name for name in exact if name not in required]
    min_properties = view.min_properties or 0
    max_properties = view.max_properties
    if min_properties > _LIST_CAP:
        # More distinct properties than Hypothesis can generate; nothing satisfies it in practice.
        return st.nothing()

    def value_for(name: str) -> SearchStrategy[JsonValue] | None:
        if name in exact:
            return exact[name]
        for pattern, value in patterns:
            if re.search(pattern, name):
                return value
        return additional

    declared = set(exact)
    name_sources = [st.from_regex(pattern) for pattern, _ in patterns]
    if additional is not None:
        name_sources.append(_property_name(ctx, view.property_names))
    extra_names = st.one_of(name_sources).filter(lambda name: name not in declared) if name_sources else None
    # A name in the additional-name set: declared by nothing and matching no pattern.
    additional_name = _property_name(ctx, view.property_names).filter(
        lambda name: name not in declared and not any(re.search(pattern, name) for pattern, _ in patterns)
    )

    def build(draw: st.DrawFn) -> dict[str, JsonValue]:
        def value(name: str) -> JsonValue:
            # `None` means the name is a forbidden additional property -> the draw is unsatisfiable.
            return draw(value_for(name) if value_for(name) is not None else st.nothing())

        result = {name: draw(exact[name]) for name in required}
        if optional:
            limit = len(optional) if max_properties is None else max(0, max_properties - len(result))
            # When extras can't fill to `minProperties`, declared optionals must cover the gap.
            floor = 0 if extra_names is not None else min(limit, max(0, min_properties - len(result)))
            for name in draw(st.lists(st.sampled_from(optional), unique=True, min_size=floor, max_size=limit)):
                result[name] = draw(exact[name])
        pending = True
        while pending:
            pending = False
            for trigger, names in dependent_required:
                if trigger in result:
                    for name in names:
                        if name not in result:
                            result[name] = value(name)
                            pending = True
        for pattern in pattern_required:
            if not any(re.search(pattern, key) for key in result):
                name = draw(st.from_regex(pattern))
                result[name] = value(name)
        for value_strategy in additional_required:
            name = draw(additional_name)
            result[name] = draw(value_strategy)
        if extra_names is not None:
            # Emit undeclared properties even past `minProperties`: a schema that only allows additional
            # properties (no declared ones) must still produce non-empty objects to exercise their values.
            lower = max(0, min_properties - len(result))
            if max_properties is None:
                upper = max(lower, _OPEN_OBJECT_MAX)
            else:
                upper = max(lower, max_properties - len(result))
            for name in draw(st.lists(extra_names, unique=True, min_size=lower, max_size=upper)):
                result[name] = value(name)
        return result

    strategy = st.composite(build)()
    # Targeted soundness checks the constructive build can't guarantee. Kept narrow (and ref-free)
    # so they never depend on validating the whole object's deep, possibly-symbolic property schemas.
    for check in _object_checks(view, min_properties, max_properties):
        strategy = strategy.filter(check)
    if needs_validation:
        # Dropped patterns leave name->value routing approximate; the ECMA-aware validator is the net.
        strategy = strategy.filter(_accepts(schema.to_json_schema()))
    return strategy


def _object_checks(
    view: jsonschema_rs.canonical.ObjectView, min_properties: int, max_properties: int | None
) -> list[Callable[[dict], bool]]:
    checks: list[Callable[[dict], bool]] = []
    if max_properties is not None:
        checks.append(lambda obj: len(obj) <= max_properties)
    if min_properties:
        checks.append(lambda obj: len(obj) >= min_properties)
    if view.property_names is not None:
        name_ok = _validator_for(view.property_names.to_json_schema()).is_valid
        checks.append(lambda obj: all(name_ok(key) for key in obj))
    dependent: list[tuple[str, Callable[[dict], bool]]] = []
    for requirement in view.requirements:
        if isinstance(requirement, jsonschema_rs.canonical.DependentSchemaRequirement):
            try:
                is_valid = _accepts(requirement.schema.to_json_schema())
            except ValueError:
                continue
            dependent.append((requirement.property, is_valid))
    if dependent:
        checks.append(lambda obj: all(trigger not in obj or ok(obj) for trigger, ok in dependent))
    return checks


def _additional_value(
    view: jsonschema_rs.canonical.ObjectView, ctx: StrategyContext
) -> SearchStrategy[JsonValue] | None:
    for c in view.constraints:
        if isinstance(c, jsonschema_rs.canonical.AdditionalPropertiesConstraint):
            if isinstance(c.schema.view(), jsonschema_rs.canonical.FalseView):
                return None
            return from_schema(c.schema, ctx)
    # Absent `additionalProperties` allows any extra property.
    return _anything(ctx)


def _property_name(ctx: StrategyContext, property_names: jsonschema_rs.CanonicalSchema | None) -> SearchStrategy[str]:
    if property_names is None:
        return _text(ctx)
    # Generate names that satisfy `propertyNames` directly rather than filtering arbitrary text.
    return from_schema(property_names, ctx)


def _combine_one_of(
    view: jsonschema_rs.canonical.AnyOfView | jsonschema_rs.canonical.OneOfView, ctx: StrategyContext
) -> SearchStrategy[JsonValue]:
    return st.one_of([from_schema(member, ctx) for member in view.schemas])


def _all_of(view: jsonschema_rs.canonical.AllOfView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    merged = reduce(lambda a, b: a.intersect(b), view.schemas)
    merged_view = merged.view()
    if not isinstance(merged_view, jsonschema_rs.canonical.AllOfView):
        return from_schema(merged, ctx)
    # Conjunction `intersect` can't reduce (e.g. number AND not-integer, array AND not-unique): lift a
    # positive branch and enforce the rest by filtering. Precision ladder -- sound with bounded retry.
    branches = merged_view.schemas
    # `negate(X)` is the positive form of a `not X` branch; re-intersecting in that form lets canonicalize
    # merge requirements (e.g. a `not {kind: const}` forcing a `kind` property) into a generatable base.
    rebuilt = reduce(
        lambda a, b: a.intersect(b),
        [
            branch.view().schema.negate() if isinstance(branch.view(), jsonschema_rs.canonical.NotView) else branch
            for branch in branches
        ],
    )
    if not isinstance(rebuilt.view(), jsonschema_rs.canonical.AllOfView):
        return from_schema(rebuilt, ctx).filter(_accepts(merged.to_json_schema()))
    # `intersect` can't fold a `{properties: {k: const}}` pin into a symbolic `$ref` object (e.g. a
    # discriminator), and that pin alone is type-open -- lifting it as the base would generate mostly
    # non-objects and filter them all out. Pin such properties constructively on an object base instead.
    positives = [b for b in branches if not isinstance(b.view(), jsonschema_rs.canonical.NotView)]
    pins: dict[str, JsonValue] = {}
    others = []
    for branch in positives:
        extracted = _object_property_pins(branch.view())
        if extracted is not None:
            pins.update(extracted)
        else:
            others.append(branch)
    # A `Const`/`Enum` branch bounds the value space to a few literals (e.g. `allOf: [{type: array}, {enum}]`
    # from a typed `enum`); generate those directly instead of lifting an open leaf and filtering everything.
    literal = next(
        (
            b
            for b in positives
            if isinstance(b.view(), (jsonschema_rs.canonical.ConstView, jsonschema_rs.canonical.EnumView))
        ),
        None,
    )
    base = literal if literal is not None else (others[0] if others else (positives[0] if positives else branches[0]))
    strategy = from_schema(base, ctx)
    if pins:
        strategy = strategy.map(lambda value: {**value, **pins} if isinstance(value, dict) else value)
    return strategy.filter(_accepts(merged.to_json_schema()))


def _object_property_pins(view: _View) -> dict[str, JsonValue] | None:
    # `{properties: {k: const v}}` (a type-open object guard) -> {k: v}; `None` for anything else.
    if not isinstance(view, jsonschema_rs.canonical.TypeGuardView) or view.type_name != "object":
        return None
    body = view.body.view()
    if not isinstance(body, jsonschema_rs.canonical.ObjectView):
        return None
    if body.requirements or body.property_names is not None:
        return None
    pins: dict[str, JsonValue] = {}
    for constraint in body.constraints:
        if not isinstance(constraint, jsonschema_rs.canonical.NamedPropertyConstraint):
            return None
        value_view = constraint.schema.view()
        if not isinstance(value_view, jsonschema_rs.canonical.ConstView):
            return None
        pins[constraint.name] = value_view.value
    return pins or None


_JSON_TYPES = ("null", "boolean", "integer", "number", "string", "array", "object")


def _is_json_type(value: JsonValue, type_name: str) -> bool:
    if type_name == "null":
        return value is None
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "array":
        return isinstance(value, list)
    return isinstance(value, dict)


def _typed_group(
    view: jsonschema_rs.canonical.TypedGroupView | jsonschema_rs.canonical.TypeGuardView, ctx: StrategyContext
) -> SearchStrategy[JsonValue]:
    body = from_schema(view.body, ctx)
    if isinstance(view, jsonschema_rs.canonical.TypeGuardView):
        # The body constrains only `type_name`; values of every other type pass freely.
        # `integer` is a subset of `number`, so a number guard constrains integers too.
        excluded = {view.type_name, "integer"} if view.type_name == "number" else {view.type_name}
        others = [_bare_type(name, ctx) for name in _JSON_TYPES if name not in excluded]
        return st.one_of([body, *others])
    # TypedGroup: the value is `type_name` AND satisfies `body`; body branches of other types
    # (e.g. the `string`/`array` arms of a `not multipleOf` negation) must be filtered out.
    return body.filter(lambda value: _is_json_type(value, view.type_name))


def _validated_against(schema: jsonschema_rs.CanonicalSchema, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    validator = _validator_for(schema.to_json_schema())
    return _anything(ctx).filter(validator.is_valid)


_LIFTERS: dict[str, _Lifter] = {
    "TrueView": _true,
    "FalseView": _false,
    "ConstView": _const,
    "EnumView": _enum,
    "IntegerView": _integer,
    "NumberView": _number,
    "MultiTypeView": _multi_type,
    "ArrayView": _array,
    "AnyOfView": _combine_one_of,
    "OneOfView": _combine_one_of,
    "AllOfView": _all_of,
    "TypedGroupView": _typed_group,
    "TypeGuardView": _typed_group,
}
