from __future__ import annotations

import math
import re
import sys
from fractions import Fraction
from functools import lru_cache
from typing import TYPE_CHECKING, cast

import jsonschema_rs
from hypothesis import strategies as st
from jsonschema_rs import canonical

from schemathesis.generation.jsonschema.context import Alphabet

if TYPE_CHECKING:
    from collections.abc import Callable
    from decimal import Decimal

    from hypothesis.strategies import SearchStrategy

    from schemathesis.core.jsonschema.types import JsonValue
    from schemathesis.generation.jsonschema.context import StrategyContext

    # A bound arrives as `Decimal` when no float spells it back.
    Numeric = int | float | Decimal


# Upper bound on properties drawn beyond the ones the schema names.
_EXTRA_KEYS = 5


class UnsupportedView(Exception):
    """A canonical node this module cannot build from; the caller falls back to `hypothesis-jsonschema`."""


class Unrepresentable:
    """A grid point with no JSON number to carry it; the caller filters it out."""


UNREPRESENTABLE = Unrepresentable()


def from_schema(schema: jsonschema_rs.CanonicalSchema, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    cached = ctx.cache.get(schema)
    if cached is None:
        cached = _build(schema, ctx)
        ctx.cache[schema] = cached
    return cached


def _build(schema: jsonschema_rs.CanonicalSchema, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    view = schema.view()
    if isinstance(view, canonical.TrueView):
        return _anything(ctx)
    if isinstance(view, canonical.FalseView):
        return st.nothing()
    if isinstance(view, canonical.ConstView):
        return st.just(cast("JsonValue", view.value))
    if isinstance(view, canonical.EnumView):
        return st.sampled_from(view.values)
    if isinstance(view, canonical.MultiTypeView):
        return st.one_of([_bare_type(name, ctx) for name in view.types])
    if isinstance(view, canonical.TypedGroupView):
        return from_schema(view.body, ctx)
    if isinstance(view, canonical.AnyOfView):
        return st.one_of([from_schema(branch, ctx) for branch in view.branches])
    if isinstance(view, canonical.IntegerView):
        return _integer(view)
    if isinstance(view, canonical.NumberView):
        return _number(view)
    if isinstance(view, canonical.StringView):
        return _string(view, ctx)
    if isinstance(view, canonical.ObjectView):
        return _object(view, ctx)
    if isinstance(view, canonical.ArrayView):
        return _array(view, ctx)
    raise UnsupportedView(schema.kind)


def _array(view: jsonschema_rs.canonical.ArrayView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    # How many elements match is a property of the array as a whole, not of any one position.
    if view.contains:
        raise UnsupportedView("array")
    element = _anything(ctx) if view.items is None else from_schema(view.items, ctx)
    if view.prefix_items:
        return _tuple(view, element, ctx)
    kwargs: dict[str, int] = {}
    if view.min_items is not None:
        kwargs["min_size"] = view.min_items
    if view.max_items is not None:
        kwargs["max_size"] = view.max_items
    if view.unique_items:
        return st.lists(element, unique_by=_json_identity, **kwargs)
    return st.lists(element, **kwargs)


def _tuple(
    view: jsonschema_rs.canonical.ArrayView, element: SearchStrategy[JsonValue], ctx: StrategyContext
) -> SearchStrategy[JsonValue]:
    """An array whose leading positions each carry their own schema."""
    # A schema past the length ceiling pins a position no array can have.
    pinned = view.prefix_items if view.max_items is None else view.prefix_items[: view.max_items]
    head = st.tuples(*[from_schema(entry, ctx) for entry in pinned])
    # Arrays shorter than the prefix are admitted too, but skipping them keeps the draw a plain
    # concatenation instead of a size-first two-step.
    kwargs: dict[str, int] = {"min_size": max(0, (view.min_items or 0) - len(pinned))}
    if view.max_items is not None:
        kwargs["max_size"] = view.max_items - len(pinned)
    if view.unique_items:
        # `unique_by` settles the tail; the filter catches what it cannot see — collisions inside the
        # prefix and across the two halves.
        tail = st.lists(element, unique_by=_json_identity, **kwargs)
        return st.tuples(head, tail).map(_concat).filter(_all_unique)
    return st.tuples(head, st.lists(element, **kwargs)).map(_concat)


def _concat(parts: tuple[tuple[JsonValue, ...], list[JsonValue]]) -> JsonValue:
    return [*parts[0], *parts[1]]


def _all_unique(values: JsonValue) -> bool:
    assert isinstance(values, list)
    return len({_json_identity(value) for value in values}) == len(values)


def _json_identity(value: JsonValue) -> object:
    """What `uniqueItems` counts as the same value."""
    # `True == 1` in Python, but `true` and `1` are different JSON values.
    if isinstance(value, bool):
        return ("boolean", value)
    # Numbers compare by value, so `1` and `1.0` are one element. Exact rationals, since rounding
    # to a float would merge distinct numbers that happen to share one.
    if isinstance(value, (int, float)):
        return ("number", _spelled(value))
    return ("json", canonical.json.to_string(value))


def _object(view: jsonschema_rs.canonical.ObjectView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    # Every remaining facet constrains keys this module draws freely, and honoring them needs a
    # joint draw over names, values and size.
    if view.pattern_properties or view.min_properties is not None or view.max_properties is not None:
        raise UnsupportedView("object")
    entries = {key: from_schema(entry, ctx) for key, entry in view.properties.items()}
    # Whatever `properties` does not name answers to `additionalProperties`, and is otherwise free.
    unnamed = _anything(ctx) if view.additional_properties is None else from_schema(view.additional_properties, ctx)
    # A key can be required without `properties` saying anything about its value.
    required = {key: entries[key] if key in entries else unnamed for key in view.required}
    optional = {key: entry for key, entry in entries.items() if key not in view.required}
    if view.property_names is not None:
        # A closed name set — what `additionalProperties: false` folds into — admits nothing else,
        # so the names it lists are the only optional keys.
        names = _closed_names(view.property_names)
        if names is None:
            raise UnsupportedView("object")
        # Sorted: draw order decides what a seed replays, and set iteration order is not stable
        # across processes.
        optional.update(dict.fromkeys(sorted(names - set(required) - set(optional)), unnamed))
        return st.fixed_dictionaries(required, optional=optional)
    named = st.fixed_dictionaries(required, optional=optional)
    known = set(view.properties) | set(view.required)
    extra = st.dictionaries(_text(ctx).filter(lambda key: key not in known), unnamed, max_size=_EXTRA_KEYS)
    return st.tuples(named, extra).map(lambda parts: {**parts[1], **parts[0]})


def _closed_names(schema: jsonschema_rs.CanonicalSchema) -> set[str] | None:
    """The finite set of admitted property names, or `None` when names are not enumerable."""
    view = schema.view()
    if isinstance(view, canonical.ConstView):
        names = {view.value}
    elif isinstance(view, canonical.EnumView):
        names = set(view.values)
    else:
        return None
    # Canonicalization drops admitted values no property name could equal.
    assert all(isinstance(name, str) for name in names)
    return names


def _integer(view: jsonschema_rs.canonical.IntegerView) -> SearchStrategy[JsonValue]:
    multiple_of = _combined_multiple_of(view.multiple_of)
    if multiple_of is None:
        return st.integers(min_value=view.minimum, max_value=view.maximum)
    # On a `p/q` grid only multiples of `p` are whole, `q` being coprime to it.
    stride = multiple_of.numerator
    low = None if view.minimum is None else -(-view.minimum // stride)
    high = None if view.maximum is None else view.maximum // stride
    return st.integers(min_value=low, max_value=high).map(lambda step: step * stride)


def _number(view: jsonschema_rs.canonical.NumberView) -> SearchStrategy[JsonValue]:
    multiple_of = _combined_multiple_of(view.multiple_of)
    if multiple_of is not None:
        steps = _steps(
            _lower_step(view.minimum, view.exclusive_minimum, multiple_of),
            _upper_step(view.maximum, view.exclusive_maximum, multiple_of),
        )
        # Grid points that no float represents round to a neighbour off the grid or outside the bounds.
        is_valid = _grid_check(view, multiple_of)
        return steps.map(lambda step: _fraction_to_json_number(step * multiple_of)).filter(is_valid)

    bounds = _representable_float_bounds(view)
    if bounds is not None:
        float_low, float_high = bounds
        return st.floats(min_value=float_low, max_value=float_high, allow_nan=False, allow_infinity=False).map(
            lambda value: value or 0.0
        )
    # No float lies within the bounds, leaving the integers that do.
    return _steps(
        _lower_step(view.minimum, view.exclusive_minimum, Fraction(1)),
        _upper_step(view.maximum, view.exclusive_maximum, Fraction(1)),
    )


def _grid_check(
    view: jsonschema_rs.canonical.NumberView, multiple_of: Fraction
) -> Callable[[int | float | Unrepresentable], bool]:
    """Accept only what every reading of the emitted number clears."""
    minimum = None if view.minimum is None else _spelled(view.minimum)
    maximum = None if view.maximum is None else _spelled(view.maximum)
    exclusive_minimum = view.exclusive_minimum
    exclusive_maximum = view.exclusive_maximum

    def is_valid(value: int | float | Unrepresentable) -> bool:
        if value is UNREPRESENTABLE:
            return False
        number = cast("int | float", value)
        spelled = _spelled(number)
        if spelled % multiple_of != 0:
            return False
        for reading in _readings(number, spelled):
            if minimum is not None and (reading <= minimum if exclusive_minimum else reading < minimum):
                return False
            if maximum is not None and (reading >= maximum if exclusive_maximum else reading > maximum):
                return False
        return True

    return is_valid


def _readings(value: int | float, spelled: Fraction) -> tuple[Fraction, ...]:
    # A reader either keeps the decimal spelled here or parses it into an `f64`, and `jsonschema-rs`
    # itself does both: the `f64` for a bound fitting an `i64`, the decimal for anything wider.
    return (spelled,) if isinstance(value, int) else (spelled, Fraction(value))


def _steps(low: int | None, high: int | None) -> SearchStrategy[int]:
    if low is not None and high is not None and low > high:
        # Canonical bounds come from binary float arithmetic; exact rationals can find the range empty.
        return st.nothing()
    return st.integers(min_value=low, max_value=high)


def _lower_step(bound: int | float | None, exclusive: bool, step_size: Fraction) -> int | None:
    if bound is None:
        return None
    quotient = _quotient(bound, step_size)
    return math.floor(quotient) + 1 if exclusive else math.ceil(quotient)


def _upper_step(bound: int | float | None, exclusive: bool, step_size: Fraction) -> int | None:
    if bound is None:
        return None
    quotient = _quotient(bound, step_size)
    return math.ceil(quotient) - 1 if exclusive else math.floor(quotient)


def _quotient(bound: int | float, step_size: Fraction) -> Fraction:
    return _spelled(bound) / step_size


def _spelled(value: Numeric) -> Fraction:
    # `str` first: a JSON number means the decimal it spells, not the binary float storing it.
    return Fraction(str(value))


def _fraction_to_json_number(value: Fraction) -> int | float | Unrepresentable:
    if value.denominator == 1:
        return value.numerator
    try:
        return float(value)
    except OverflowError:
        # Whole grid points ride out as exact integers, fractional ones need a float to land in.
        return UNREPRESENTABLE


def _representable_float_bounds(
    view: jsonschema_rs.canonical.NumberView,
) -> tuple[float | None, float | None] | None:
    low = None
    if view.minimum is not None:
        if view.minimum > sys.float_info.max:
            return None
        if view.minimum >= -sys.float_info.max:
            low = float(view.minimum)
            # An exclusive bound rules out its own rounding; an inclusive one only rules out a float
            # landing under it.
            minimum = _spelled(view.minimum)
            if view.exclusive_minimum or any(reading < minimum for reading in _readings(low, _spelled(low))):
                low = math.nextafter(low, math.inf)

    high = None
    if view.maximum is not None:
        if view.maximum < -sys.float_info.max:
            return None
        if view.maximum <= sys.float_info.max:
            high = float(view.maximum)
            maximum = _spelled(view.maximum)
            if view.exclusive_maximum or any(reading > maximum for reading in _readings(high, _spelled(high))):
                high = math.nextafter(high, -math.inf)

    if low is not None and high is not None and low > high:
        return None
    if (low is not None and math.isinf(low)) or (high is not None and math.isinf(high)):
        # Stepping off the last float leaves the range; only integers clear the bound.
        return None
    return low, high


def _combined_multiple_of(values: list[float]) -> Fraction | None:
    # Exact rationals: `step * 0.1` in binary floats lands off the grid two times out of five. Several
    # divisors admit exactly the multiples of their least common multiple.
    combined = None
    for value in values:
        candidate = Fraction(str(value))
        if combined is None:
            combined = candidate
        else:
            combined = Fraction(
                math.lcm(combined.numerator, candidate.numerator),
                math.gcd(combined.denominator, candidate.denominator),
            )
    return combined


def _string(view: jsonschema_rs.canonical.StringView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    # Content facets narrow the admitted strings, and driving them needs generators this module has
    # no access to.
    if view.content_media_types or view.content_encodings:
        raise UnsupportedView("string")
    # A name with no generator behind it is an annotation and leaves the strings unconstrained.
    known = [name for name in view.formats if name in ctx.formats]
    if len(known) > 1:
        # Two generators, one value: satisfying both needs an intersection this module cannot build.
        raise UnsupportedView("string")
    if known:
        return _formatted(known[0], view, ctx)
    if not view.patterns:
        kwargs: dict[str, int] = {}
        if view.min_length is not None:
            kwargs["min_size"] = view.min_length
        if view.max_length is not None:
            kwargs["max_size"] = view.max_length
        return _text(ctx, **kwargs)
    if len(view.patterns) > 1 or not _compiles(view.patterns[0]):
        # Intersecting patterns need a conjunctive rewrite, and a pattern Python `re` rejects (e.g. ECMA
        # `\p{L}`) can't drive generation at all.
        raise UnsupportedView("string")
    # `fullmatch` avoids `$` matching before a trailing newline (which the validator rejects);
    # full matches are a subset of the search matches the schema accepts, so it stays sound.
    strategy = st.from_regex(view.patterns[0], fullmatch=True, alphabet=ctx.alphabet.as_strategy())
    # Length is normally folded into the pattern upstream; this filter is the soundness net.
    return _within_length(strategy, view)


def _formatted(name: str, view: jsonschema_rs.canonical.StringView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    """Values from the generator registered for `name`, narrowed to the facets around it."""
    strategy = ctx.formats[name]
    for pattern in view.patterns:
        if not _compiles(pattern):
            raise UnsupportedView("string")
        # A format generator cannot be steered, so the pattern can only be filtered for. `search`,
        # not `fullmatch`: an unanchored pattern admits a match anywhere in the value.
        strategy = strategy.filter(re.compile(pattern).search)
    return _within_length(strategy, view)


def _within_length(
    strategy: SearchStrategy[JsonValue], view: jsonschema_rs.canonical.StringView
) -> SearchStrategy[JsonValue]:
    if view.min_length is None and view.max_length is None:
        return strategy
    low = view.min_length or 0
    high = math.inf if view.max_length is None else view.max_length
    return strategy.filter(lambda value: low <= len(value) <= high)


def _compiles(pattern: str) -> bool:
    try:
        re.compile(pattern)
        return True
    except re.error:
        return False


def _anything(ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    return _anything_for(ctx.alphabet.allow_x00, ctx.alphabet.codec)


@lru_cache
def _anything_for(allow_x00: bool, codec: str | None) -> SearchStrategy[JsonValue]:
    # Arbitrary JSON value; containers bounded to keep draws cheap. Assembling the recursive strategy
    # costs far more than every other lifter combined, and it depends only on the alphabet.
    text = st.text(alphabet=Alphabet(allow_x00=allow_x00, codec=codec).as_strategy())
    return st.recursive(
        st.none()
        | st.booleans()
        | st.integers()
        | st.floats(allow_nan=False, allow_infinity=False).map(lambda x: x or 0.0)
        | text,
        lambda children: st.lists(children, max_size=3) | st.dictionaries(text, children, max_size=3),
    )


def _text(ctx: StrategyContext, **kwargs: int) -> SearchStrategy[str]:
    return st.text(alphabet=ctx.alphabet.as_strategy(), **kwargs)


def _bare_type(name: str, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    match name:
        case "null":
            return st.none()
        case "boolean":
            return st.booleans()
        case "integer":
            return st.integers()
        case "number":
            return st.floats(allow_nan=False, allow_infinity=False).map(lambda x: x or 0.0)
        case "string":
            return _text(ctx)
        case "array":
            return st.lists(_anything(ctx))
        case _:
            return st.dictionaries(_text(ctx), _anything(ctx))
