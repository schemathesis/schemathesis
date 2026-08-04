from __future__ import annotations

import base64
import json
import math
import re
import sys
from dataclasses import dataclass, replace
from fractions import Fraction
from functools import lru_cache
from typing import TYPE_CHECKING, NamedTuple, cast

import jsonschema_rs
from hypothesis import strategies as st
from hypothesis.errors import InvalidArgument
from hypothesis.strategies._internal.deferred import DeferredStrategy
from jsonschema_rs import canonical

from schemathesis.core.errors import InvalidSchema
from schemathesis.core.jsonschema import (
    CANONICALIZE_DRAFT_BY_VALIDATOR,
    compile_ecma_pattern,
    make_validator,
    make_validator_for,
)
from schemathesis.generation.jsonschema.context import Alphabet
from schemathesis.specs.openapi.patterns import pattern_length_bounds

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from decimal import Decimal

    from hypothesis.strategies import SearchStrategy

    from schemathesis.core.jsonschema.types import JsonValue
    from schemathesis.generation.jsonschema.context import StrategyContext

    # A bound arrives as `Decimal` when no float spells it back.
    Numeric = int | float | Decimal

    # Values a position or demand can draw from, and how many distinct ones it needs. `None` where
    # they cannot be counted, and so can never run out.
    Need = tuple[frozenset[object] | None, int]


# Upper bound on properties drawn beyond the ones the schema names.
_EXTRA_KEYS = 5
_VALIDATOR_BY_CANONICALIZE_DRAFT = {draft: cls for cls, draft in CANONICALIZE_DRAFT_BY_VALIDATOR.items()}


def _countable(bound: int | None) -> int | None:
    """A size bound Hypothesis can be handed, or `None` when it runs past what a size can count."""
    # Sizes are averaged as floats, so a bound past this range fails the conversion before any check.
    return bound if bound is None or bound <= sys.maxsize else None


class UnsupportedView(Exception):
    """A canonical node this module cannot build from; the caller decides what to do instead."""


class _PatternProperty(NamedTuple):
    """One `patternProperties` entry: the names it claims, and the values it admits."""

    claims: Callable[[str], bool]
    values: SearchStrategy[JsonValue]
    schema: jsonschema_rs.CanonicalSchema


class Unrepresentable:
    """A grid point with no JSON number to carry it; the caller filters it out."""


UNREPRESENTABLE = Unrepresentable()


class _Node(DeferredStrategy):
    """One schema, named rather than spelled out."""

    # A schema reached from many places, or from itself, would otherwise spell out the same subtree at
    # every mention, growing what Hypothesis prints - a rejected filter, a failed draw - past any use.

    def __init__(self, strategy: SearchStrategy[JsonValue], kind: str) -> None:
        super().__init__(lambda: strategy)
        self.kind = kind

    def __repr__(self) -> str:
        return f"schema({self.kind})"


def from_schema(schema: jsonschema_rs.CanonicalSchema, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    cached = ctx.cache.get(schema)
    if cached is None:
        cached = _Node(_build(schema, ctx), schema.kind)
        ctx.cache[schema] = cached
    return cached


def _simplest_first(value: JsonValue) -> tuple[int, float, str]:
    """Sort key putting the value a shrunk failure should report first: scalars, then short before long."""
    if value is None:
        return (0, 0, "")
    if isinstance(value, bool):
        return (1, int(value), "")
    if isinstance(value, (int, float)):
        return (2 if int(value) == value else 3, abs(value), "" if value >= 0 else "-")
    if isinstance(value, str):
        return (4, len(value), value)
    return (5 if isinstance(value, list) else 6, len(value), jsonschema_rs.canonical.json.to_string(value))


def _build(schema: jsonschema_rs.CanonicalSchema, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    view = schema.view()
    if isinstance(view, canonical.TrueView):
        return _anything(ctx)
    if isinstance(view, canonical.FalseView):
        return st.nothing()
    if isinstance(view, canonical.ConstView):
        return st.just(cast("JsonValue", view.value))
    if isinstance(view, canonical.EnumView):
        return st.sampled_from(sorted(view.values, key=_simplest_first))
    if isinstance(view, canonical.MultiTypeView):
        return st.one_of([_bare_type(name, ctx) for name in view.types])
    if isinstance(view, canonical.TypedGroupView):
        # The node means "type is `type_name` *and* body"; canonicalization folds the type in first,
        # so this is a net, not the filter doing the work.
        return from_schema(view.body, ctx).filter(_TYPE_CHECKS[view.type_name])
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
        return _array(schema, view, ctx)
    if isinstance(view, canonical.ReferenceView):
        return _reference(schema, view, ctx)
    if isinstance(view, canonical.AllOfView):
        return _all_of(view, ctx)
    if isinstance(view, canonical.OneOfView):
        return _one_of(view, ctx)
    raise UnsupportedView(schema.kind)


def _target(schema: jsonschema_rs.CanonicalSchema, uri: str, ctx: StrategyContext) -> jsonschema_rs.CanonicalSchema:
    """The schema a pointer names."""
    # `#` is the one pointer with no definition behind it: it names the document itself.
    if uri == "#":
        return ctx.root
    target = schema.definition(uri)
    # Canonicalization refuses a pointer it cannot resolve, so the target is here.
    assert target is not None
    return target


def _reference(
    schema: jsonschema_rs.CanonicalSchema, view: jsonschema_rs.canonical.ReferenceView, ctx: StrategyContext
) -> SearchStrategy[JsonValue]:
    """Values admitted by the schema the pointer names."""
    target = _target(schema, view.uri, ctx)
    pending = ctx.pending.get(view.uri)
    if pending is not None:
        # A pointer back into what is still being built. Spelling the rest of the value lazily lets
        # a draw stop descending, where unrolling it here never would, and every pointer to this
        # target gets that same strategy - so a cycle in the schema becomes one Hypothesis can see.
        ctx.cyclic = True
        return pending
    ctx.pending[view.uri] = st.deferred(lambda: from_schema(target, ctx))
    try:
        return from_schema(target, ctx)
    finally:
        del ctx.pending[view.uri]


def _all_of(view: jsonschema_rs.canonical.AllOfView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    """Values every branch admits.

    What reaches here is an `allOf` canonicalization left standing, which happens where a branch is
    a pointer: folding one would mean unrolling whatever it names. Following the pointers gives the
    intersection back, one branch at a time, until only pointers leading back into the value remain.
    """
    followed = []
    opened = []
    for branch in view.branches:
        branch_view = branch.view()
        if isinstance(branch_view, canonical.ReferenceView) and branch_view.uri not in ctx.following:
            ctx.following.add(branch_view.uri)
            opened.append(branch_view.uri)
            followed.append(_target(branch, branch_view.uri, ctx))
        else:
            followed.append(branch)
    try:
        if opened:
            # Still an `allOf` where a followed branch pointed on; the next round follows those.
            return from_schema(_intersection(followed), ctx)
        for index, branch in enumerate(followed):
            branch_view = branch.view()
            if isinstance(branch_view, canonical.OneOfView):
                # `allOf[X, oneOf[A, B]]` admits what `oneOf[allOf[X, A], allOf[X, B]]` admits, and
                # carrying the rest into every branch lets each one draw what it needs - where the
                # loose half driving alone would have to land inside a branch by chance.
                rest = followed[:index] + followed[index + 1 :]
                return _exclusive(
                    branch_view.branches,
                    [from_schema(_intersection([*rest, sub]), ctx) for sub in branch_view.branches],
                )
        # Every branch is a pointer back into the value being built, so none of them can drive a
        # draw on its own. The first one does, and the rest judge what it gives.
        driver, *rest = followed
        strategy = from_schema(driver, ctx)
        for other in rest:
            strategy = strategy.filter(jsonschema_rs.validator_for(other.to_json_schema()).is_valid)
        return strategy
    finally:
        ctx.following.difference_update(opened)


def _one_of(view: jsonschema_rs.canonical.OneOfView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    """Values exactly one branch admits."""
    return _exclusive(view.branches, [from_schema(branch, ctx) for branch in view.branches])


def _exclusive(
    branches: Sequence[jsonschema_rs.CanonicalSchema], strategies: list[SearchStrategy[JsonValue]]
) -> SearchStrategy[JsonValue]:
    """Values from each strategy that no branch other than its own admits."""
    validators = [jsonschema_rs.validator_for(branch.to_json_schema()) for branch in branches]
    narrowed = []
    for index, strategy in enumerate(strategies):
        # What the other branches also admit belongs to no branch alone.
        others = validators[:index] + validators[index + 1 :]
        narrowed.append(
            strategy.filter(lambda value, others=others: not any(other.is_valid(value) for other in others))
        )
    return st.one_of(narrowed)


def _intersection(branches: list[jsonschema_rs.CanonicalSchema]) -> jsonschema_rs.CanonicalSchema:
    """One schema admitting what every branch admits."""
    result = branches[0]
    for branch in branches[1:]:
        result = result.intersect(branch)
    return result


@dataclass(frozen=True, slots=True)
class _Demand:
    """One `contains` demand, together with what an array can draw to meet it."""

    schema: jsonschema_rs.CanonicalSchema
    minimum: int
    ceiling: int | None
    # Values meeting both the demand and `items`, or `None` when nothing appended can meet it.
    element: SearchStrategy[JsonValue] | None = None
    # The distinct values it can draw from, when they can be counted.
    supply: frozenset[object] | None = None
    # Whether the element provably stays off every other bounded demand.
    counted: bool = True


@dataclass(slots=True)
class _Placement:
    """How one demand's matches are spread over an array's positions."""

    demand: _Demand
    # Opening positions narrowed to meet the demand.
    carried: int = 0
    # Positions appended past the opening ones to meet the rest of it.
    placed: int = 0
    # Further appended positions it may take on where nothing else can fill them.
    slack: int = 0


@dataclass(slots=True)
class _Layout:
    """One assignment of every demand's matches to an array's positions."""

    # The opening positions, each narrowed towards or away from the demands.
    entries: list[jsonschema_rs.CanonicalSchema]
    placements: list[_Placement]
    # Whether every position's match count is one this steered rather than guessed.
    counted: bool
    # Whether the opening positions are the whole array, with nothing able to follow them.
    closed: bool


def _array(
    schema: jsonschema_rs.CanonicalSchema, view: jsonschema_rs.canonical.ArrayView, ctx: StrategyContext
) -> SearchStrategy[JsonValue]:
    if view.min_items is not None and _countable(view.min_items) is None:
        # No array can be that long.
        return st.nothing()
    if view.prefix_items or view.contains:
        return _with_fixed_elements(schema, view, ctx)
    element = _element(view.items, ctx)
    kwargs: dict[str, int] = {}
    if view.min_items is not None:
        kwargs["min_size"] = view.min_items
    max_items = _countable(view.max_items)
    if max_items is not None:
        kwargs["max_size"] = max_items
    # `st.lists` refuses either bound over an element strategy drawing nothing, so the bounds are
    # answered here instead. Skipped while a pointer target is still being built, where `is_empty`
    # would force a strategy that is not there yet.
    if kwargs and not ctx.pending and element.is_empty:
        # No position can be filled, so the empty array is the only value the bounds may allow.
        return st.nothing() if kwargs.get("min_size") else st.just([])
    if view.unique_items:
        return st.lists(element, unique_by=_json_identity, **kwargs)
    return st.lists(element, **kwargs)


def _with_fixed_elements(
    schema: jsonschema_rs.CanonicalSchema, view: jsonschema_rs.canonical.ArrayView, ctx: StrategyContext
) -> SearchStrategy[JsonValue]:
    """An array whose opening positions the schema names or demands, followed by free ones."""
    max_items = _countable(view.max_items)
    # A schema past the length ceiling pins a position no array can have.
    entries = list(view.prefix_items if max_items is None else view.prefix_items[:max_items])
    demands = [_Demand(item.schema, _minimum_contains(item), item.max_contains) for item in view.contains]
    if view.unique_items:
        # One element can meet several demands at once; folding those spares a unique array a repeat.
        demands = _merged(demands, view.items)
    # Placing the demanded elements beats filtering: `items` on its own may match rarely, or never.
    bounded = [demand.schema for demand in demands if demand.ceiling is not None]
    resolved = []
    for demand in demands:
        avoid = [other for other in bounded if other is not demand.schema]
        element, counted = _matching(view.items, demand.schema, avoid, ctx)
        supply = _supply(view.items, demand.schema) if view.unique_items else None
        resolved.append(replace(demand, element=element, supply=supply, counted=counted))
    demands = resolved

    closed = False
    if view.unique_items:
        # One element can count for several demands, and matches can always be appended, so a demand
        # only needs its own domain to hold enough distinct values.
        if any(demand.supply is not None and len(demand.supply) < demand.minimum for demand in demands):
            return st.nothing()
        limit = _distinct_limit(entries, demands)
        if limit < len(entries):
            # The prefix runs out of distinct values there, and the next position could take none.
            entries = entries[:limit]
            closed = True

    # Carrying a demand reaches the shortest array; appending keeps positions free to draw what they
    # admit. Neither dominates; `one_of` shrinks toward the first.
    candidates = [_laid_out(entries, demands, closed=closed, carry=True)]
    if entries:
        candidates.append(_laid_out(entries, demands, closed=closed, carry=False))
    layouts = [layout for layout in candidates if layout is not None]
    if len(layouts) == 2 and _carried(layouts[0]) == _carried(layouts[1]):
        # Nothing was carried that would otherwise have been appended, so the two coincide.
        layouts.pop()
    # Arrays shorter than the prefix are instances too, whenever the truncation still carries every
    # demand — and when a position admits nothing, the only ones.
    shorter = [
        layout
        for length in range(view.min_items or 0, len(entries))
        if (layout := _laid_out(entries[:length], demands, closed=True, carry=True)) is not None
    ]
    layouts = [*shorter, *layouts]
    return st.one_of([_from_layout(schema, view, layout, ctx) for layout in layouts])


def _laid_out(
    entries: list[jsonschema_rs.CanonicalSchema], demands: list[_Demand], *, closed: bool, carry: bool
) -> _Layout | None:
    """Assign each demand's matches to positions, or `None` when no array clears the schema this way."""
    entries = list(entries)
    placements = []
    counted = all(demand.counted for demand in demands)
    for demand in demands:
        placement = _Placement(demand)
        appendable = demand.element is not None and not closed
        for index, entry in enumerate(entries):
            if _covers(demand.schema, entry):
                # Every value this position admits meets the demand, so it carries one.
                entries[index] = _narrowed(entry, demand.schema) or entry
                placement.carried += 1
                continue
            if placement.carried < demand.minimum and (carry or not appendable):
                # The only way to meet it when nothing can be appended, the shortest array otherwise.
                narrowed = _narrowed(entry, demand.schema)
                if narrowed is not None:
                    entries[index] = narrowed
                    placement.carried += 1
                    continue
            if demand.ceiling is not None:
                # A position matching by chance would push the count past the ceiling.
                without = _narrowed(entry, demand.schema, negate=True)
                if without is None:
                    counted = False
                else:
                    entries[index] = without
        placement.placed = max(0, demand.minimum - placement.carried)
        if demand.ceiling is not None and placement.carried + placement.placed > demand.ceiling:
            # More positions meet the demand than it admits: no array clears this schema.
            return None
        if placement.placed and not appendable:
            return None
        placements.append(placement)
    return _Layout(entries, placements, counted=counted, closed=closed)


def _from_layout(
    schema: jsonschema_rs.CanonicalSchema,
    view: jsonschema_rs.canonical.ArrayView,
    layout: _Layout,
    ctx: StrategyContext,
) -> SearchStrategy[JsonValue]:
    """The arrays one layout admits, drawn as its parts laid end to end."""
    minimum = view.min_items or 0
    max_items = _countable(view.max_items)
    fixed = _spelled_out(layout)
    if max_items is not None and max_items < fixed:
        # The spelled-out elements alone overflow the length ceiling.
        return st.nothing()
    if layout.closed or max_items == fixed:
        free: SearchStrategy[JsonValue] = st.nothing()
        free_values = None
    else:
        free, free_values = _free(view, layout.placements, ctx)
    if layout.closed:
        # The opening positions are the whole array, so the lower size bound has to fall within them.
        if minimum > fixed:
            return st.nothing()
    elif free.is_empty:
        # The demanded elements carry the length: the shortfall first, then slack under the ceilings.
        if not _carry(layout.placements, minimum - fixed, unique=view.unique_items):
            return st.nothing()
        fixed = _spelled_out(layout)
        headroom = None if max_items is None else max_items - fixed
        for placement in layout.placements:
            room = _room(placement, unique=view.unique_items)
            if placement.demand.element is None or not room:
                continue
            placement.slack = room if headroom is None else min(room, headroom)
            if headroom is not None:
                headroom -= placement.slack
    elif view.unique_items and free_values is not None and minimum - fixed > len(free_values):
        # The tail holds fewer distinct values than the floor asks; the demanded elements carry the rest.
        if not _carry(layout.placements, minimum - fixed - len(free_values), unique=view.unique_items):
            return st.nothing()
        fixed = _spelled_out(layout)

    # No part for absent opening positions: an empty `st.tuples` still costs a draw.
    parts: list[SearchStrategy[Sequence[JsonValue]]] = []
    if layout.entries:
        parts.append(st.tuples(*[from_schema(entry, ctx) for entry in layout.entries]))
    parts.extend(
        _repeated(placement, unique=view.unique_items)
        for placement in layout.placements
        if placement.placed or placement.slack
    )
    if free.is_empty:
        # No tail part: `st.lists` refuses a positive ceiling over an element strategy drawing nothing.
        return _joined(parts, unique=view.unique_items, checked=layout.counted, schema=schema)
    kwargs: dict[str, int] = {"min_size": max(0, minimum - fixed)}
    if max_items is not None:
        kwargs["max_size"] = max_items - fixed
    # `unique_by` settles the free positions; collisions across parts are left to the joined filter.
    distinct = {"unique_by": _json_identity} if view.unique_items else {}
    parts.append(st.lists(free, **distinct, **kwargs))
    return _joined(parts, unique=view.unique_items, checked=layout.counted, schema=schema)


def _spelled_out(layout: _Layout) -> int:
    """How many positions the layout names outright, before any free ones follow."""
    return len(layout.entries) + sum(placement.placed for placement in layout.placements)


def _carried(layout: _Layout) -> list[int]:
    """How many opening positions each demand took on, in demand order."""
    return [placement.carried for placement in layout.placements]


def _joined(
    parts: list[SearchStrategy[Sequence[JsonValue]]],
    *,
    unique: bool,
    checked: bool,
    schema: jsonschema_rs.CanonicalSchema,
) -> SearchStrategy[JsonValue]:
    """The parts of an array, drawn together and laid end to end."""
    drawn: SearchStrategy = st.tuples(*parts)
    if unique:
        drawn = drawn.filter(_parts_unique)
    strategy: SearchStrategy[JsonValue] = drawn.map(_concat)
    if not checked:
        # A position could not be steered clear of a bounded demand, so its final match count is
        # confirmed against the schema itself; the draw is built to clear it, so this rejects rarely.
        strategy = strategy.filter(_validator(schema))
    return strategy


def _merged(demands: list[_Demand], items: jsonschema_rs.CanonicalSchema | None) -> list[_Demand]:
    """Demands that one element could meet together, folded into one."""
    merged: list[_Demand] = []
    for demand in demands:
        for position, other in enumerate(merged):
            # A ceiling counts matches of the original demand, which the fold would stop tracking.
            if demand.minimum == other.minimum == 1 and demand.ceiling is None and other.ceiling is None:
                joint = _narrowed(other.schema, demand.schema)
                # Within `items` the two may share nothing even when the joint stands on its own.
                if joint is not None and (items is None or _narrowed(items, joint) is not None):
                    merged[position] = replace(other, schema=joint)
                    break
            # A wider demand with no ceiling and no greater floor is met by the stricter one's elements.
            if demand.ceiling is None and demand.minimum <= other.minimum and _covers(demand.schema, other.schema):
                break
            if other.ceiling is None and other.minimum <= demand.minimum and _covers(other.schema, demand.schema):
                merged[position] = demand
                break
        else:
            merged.append(demand)
    return merged


def _distinct_limit(entries: list[jsonschema_rs.CanonicalSchema], demands: list[_Demand]) -> int:
    """How many opening positions can hold values distinct from one another."""
    limit = len(entries)
    values = [_finite_values(entry) for entry in entries]
    while limit and not _feasible([(entry, 1) for entry in values[:limit]] + _appended_needs(values[:limit], demands)):
        limit -= 1
    return limit


def _appended_needs(values: list[frozenset[object] | None], demands: list[_Demand]) -> list[Need]:
    """What each demand needs of its own, past the opening positions already meeting it."""
    needs: list[Need] = []
    for demand in demands:
        if demand.supply is None:
            needs.append((None, demand.minimum))
            continue
        # A position admitting nothing outside the demand meets it without a value of its own.
        # Sufficient, not complete: undercounting only shortens the array.
        carried = sum(1 for entry in values if entry is not None and entry <= demand.supply)
        needs.append((demand.supply, max(0, demand.minimum - carried)))
    return needs


def _feasible(needs: list[Need]) -> bool:
    """Whether every position can be handed a value of its own."""
    # Only a finite value set can force a repeat. Exact bipartite matching: greedy assignment misses
    # shapes like `{1,2} {1,2} {3,4} {1,3}`, calling a satisfiable schema empty.
    units = [values for values, count in needs if values is not None for _ in range(count)]
    universe = {value for values in units for value in values}
    if len(units) > 16 or len(universe) > 64:
        # Too wide to force a repeat cheaply; the draws settle it.
        return True
    owner: dict[object, int] = {}

    def claim(unit: int, seen: set[object]) -> bool:
        for value in sorted(units[unit] - seen, key=repr):
            seen.add(value)
            if value not in owner or claim(owner[value], seen):
                owner[value] = unit
                return True
        return False

    return all(claim(unit, set()) for unit in range(len(units)))


def _carry(placements: list[_Placement], shortfall: int, *, unique: bool) -> bool:
    """Place more demanded elements to reach the lower size bound, as far as the ceilings allow."""
    for placement in placements:
        if shortfall <= 0:
            break
        # A demand nothing appended can meet still leaves the rest to absorb the shortfall.
        if placement.demand.element is None:
            continue
        room = _room(placement, unique=unique)
        take = shortfall if room is None else min(shortfall, room)
        placement.placed += take
        shortfall -= take
    return shortfall <= 0


def _room(placement: _Placement, *, unique: bool) -> int | None:
    """How many more of this demand's elements fit, or `None` when nothing bounds them."""
    demand = placement.demand
    taken = placement.carried + placement.placed
    room = None if demand.ceiling is None else demand.ceiling - taken
    if unique and demand.supply is not None:
        # Distinct values also run out where the domain does, whatever the ceiling says.
        left = len(demand.supply) - taken
        room = left if room is None else min(room, left)
    return room


def _matching(
    items: jsonschema_rs.CanonicalSchema | None,
    demand: jsonschema_rs.CanonicalSchema,
    avoid: list[jsonschema_rs.CanonicalSchema],
    ctx: StrategyContext,
) -> tuple[SearchStrategy[JsonValue] | None, bool]:
    """Values clearing the element schema and the demand, and whether they provably stay off `avoid`."""
    schema = _intersected(items, demand)
    if not schema.is_satisfiable():
        return None, True
    steered: jsonschema_rs.CanonicalSchema | None = schema
    for other in avoid:
        steered = None if steered is None else _narrowed(steered, other, negate=True)
    if steered is not None:
        try:
            return from_schema(steered, ctx), True
        except UnsupportedView:
            pass
    counted = not avoid
    try:
        return from_schema(schema, ctx), counted
    except UnsupportedView:
        pass
    # The intersection is not one this builds from, so the demand narrows the elements by filter.
    return _element(items, ctx).filter(_validator(demand)), counted


def _free(
    view: jsonschema_rs.canonical.ArrayView, placements: list[_Placement], ctx: StrategyContext
) -> tuple[SearchStrategy[JsonValue], frozenset[object] | None]:
    """What `items` admits minus every bounded demand, with its distinct values when countable."""
    bounded = [placement.demand.schema for placement in placements if placement.demand.ceiling is not None]
    if not bounded:
        values = None if view.items is None else _finite_values(view.items)
        return _element(view.items, ctx), values
    # Filler that cannot match holds the ceiling without a counting filter.
    remaining = view.items
    for demand in bounded:
        subtracted = _subtracted(remaining, demand)
        if subtracted is None:
            break
        remaining = subtracted
    else:
        return from_schema(remaining, ctx), _finite_values(remaining)
    if any(_covers(demand, view.items) for demand in bounded):
        # A demand admits everything `items` does; the matches carry the array on their own.
        return st.nothing(), None
    # What is left of `items` is not one this builds from, so the demands become a filter.
    checks = [_validator(demand) for demand in bounded]
    return _element(view.items, ctx).filter(lambda value: not any(check(value) for check in checks)), None


# Bounded, small: the layouts revisit few pairs; equality carries the definition maps, so a hit is exact.
@lru_cache(maxsize=512)
def _covers(demand: jsonschema_rs.CanonicalSchema, items: jsonschema_rs.CanonicalSchema | None) -> bool:
    """Whether the demand admits every value the element schema does."""
    if items is None:
        return isinstance(demand.view(), canonical.TrueView)
    narrowed = _narrowed(items, demand)
    return narrowed is not None and narrowed.to_json_schema() == items.to_json_schema()


@lru_cache(maxsize=512)
def _narrowed(
    left: jsonschema_rs.CanonicalSchema, right: jsonschema_rs.CanonicalSchema, *, negate: bool = False
) -> jsonschema_rs.CanonicalSchema | None:
    """`left` narrowed to — or away from — `right`, or `None` when nothing usable is left of it."""
    schema = _subtracted(left, right) if negate else _intersected(left, right)
    if schema is None or not schema.is_satisfiable():
        return None
    return schema


def _repeated(placement: _Placement, *, unique: bool) -> SearchStrategy[list[JsonValue]]:
    """One demand's matches, drawn together rather than as a strategy per position."""
    element = placement.demand.element
    # A placement only ever takes positions the demand can fill; `_laid_out` gives up otherwise.
    assert element is not None
    count = placement.placed
    kwargs = {"unique_by": _json_identity} if unique else {}
    repeated = st.lists(element, min_size=count, max_size=count + placement.slack, **kwargs)
    try:
        repeated.validate()
    except InvalidArgument as exc:
        # No looser strategy to filter here: the demand is for more elements than Hypothesis draws
        # in one value, and an array shorter than `minContains` would not clear the schema.
        raise InvalidSchema(f"Cannot generate an array with {count} demanded elements") from exc
    return repeated


def _element(items: jsonschema_rs.CanonicalSchema | None, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    return _anything(ctx) if items is None else from_schema(items, ctx)


# The shared factory applies the project-wide kwargs — format assertions and the fancy-regex
# engine — so the filter judges a value exactly the way conformance checks do.
@lru_cache(maxsize=4096)
def _validator(schema: jsonschema_rs.CanonicalSchema) -> Callable[[JsonValue], bool]:
    return make_validator_for(schema.to_json_schema()).is_valid


def _supply(
    items: jsonschema_rs.CanonicalSchema | None, demand: jsonschema_rs.CanonicalSchema
) -> frozenset[object] | None:
    """The distinct values an element meeting the demand can take, when they can be counted."""
    view = demand.view()
    if isinstance(view, canonical.ConstView):
        raw = [cast("JsonValue", view.value)]
    elif isinstance(view, canonical.EnumView):
        raw = list(view.values)
    else:
        return None
    if items is not None:
        # An element also answers to `items`; a demanded value it rejects is not drawable.
        admits = _validator(items)
        raw = [value for value in raw if admits(value)]
    return frozenset(_json_identity(value) for value in raw)


@lru_cache(maxsize=512)
def _finite_values(schema: jsonschema_rs.CanonicalSchema) -> frozenset[object] | None:
    """The values this schema admits, as uniqueness keys, or `None` when they are not enumerable."""
    view = schema.view()
    if isinstance(view, canonical.ConstView):
        return frozenset({_json_identity(cast("JsonValue", view.value))})
    if isinstance(view, canonical.EnumView):
        return frozenset(_json_identity(value) for value in view.values)
    return None


def _minimum_contains(demand: jsonschema_rs.canonical.ContainsView) -> int:
    return 1 if demand.min_contains is None else demand.min_contains


def _intersected(
    left: jsonschema_rs.CanonicalSchema | None, right: jsonschema_rs.CanonicalSchema
) -> jsonschema_rs.CanonicalSchema:
    """Both sides as one schema."""
    return right if left is None else left.intersect(right)


def _subtracted(
    left: jsonschema_rs.CanonicalSchema | None, right: jsonschema_rs.CanonicalSchema
) -> jsonschema_rs.CanonicalSchema | None:
    """`left` without what `right` admits, or `None` where the complement cannot be spelled."""
    negated = right.negate()
    if negated is None:
        return None
    return _intersected(left, negated)


def _concat(parts: tuple[Sequence[JsonValue], ...]) -> JsonValue:
    return [value for part in parts for value in part]


def _parts_unique(parts: tuple[Sequence[JsonValue], ...]) -> bool:
    # `unique_by` settled each part on its own; only collisions across them are left to catch.
    seen: set[object] = set()
    for part in parts:
        keys = {_json_identity(value) for value in part}
        if len(keys) != len(part) or keys & seen:
            return False
        seen |= keys
    return True


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
    if view.min_properties is not None and _countable(view.min_properties) is None:
        # No object can carry that many properties.
        return st.nothing()
    entries = {key: from_schema(entry, ctx) for key, entry in view.properties.items()}
    # Whatever `properties` does not name answers to `additionalProperties`, and is otherwise free.
    unnamed = _anything(ctx) if view.additional_properties is None else from_schema(view.additional_properties, ctx)
    value_for = _value_source(view, unnamed, ctx)
    # A key can be required without `properties` saying anything about its value.
    required = {key: entries[key] if key in entries else value_for(key) for key in view.required}
    optional = {key: entry for key, entry in entries.items() if key not in view.required}
    names = None if view.property_names is None else _closed_names(view.property_names)
    if names is not None:
        # A closed name set admits nothing else, so its names are the only optional keys.
        # Sorted: set iteration order is not stable across processes, and draw order decides replays.
        optional.update({name: value_for(name) for name in sorted(names - set(required) - set(optional))})
        extra = None
    else:
        known = set(view.properties) | set(view.required)
        free_names = _free_names(view, ctx).filter(lambda key: key not in known)
        if view.pattern_properties:
            # Which schemas a key answers to is decided by its own name, so name and value are one draw.
            extra = free_names.flatmap(lambda name: st.tuples(st.just(name), value_for(name)))
        else:
            extra = st.tuples(free_names, unnamed)
    max_properties = _countable(view.max_properties)
    if view.min_properties is None and max_properties is None:
        # A coin per optional key lands on half of them, which compounds at every nesting level.
        # Built here rather than per draw, where it would be one strategy per generated value.
        optional_keys = sorted(optional)
        picker = st.sets(st.sampled_from(optional_keys), max_size=len(optional_keys)) if optional_keys else None
        named = _named_object(required, optional, picker)
        if extra is None:
            return named
        return st.tuples(named, _collect(extra, 0, _EXTRA_KEYS)).map(lambda parts: {**parts[1], **parts[0]})
    return _sized_object(required, optional, extra, view.min_properties or 0, max_properties)


@st.composite  # type: ignore[untyped-decorator]
def _named_object(
    draw: st.DrawFn,
    required: dict[str, SearchStrategy[JsonValue]],
    optional: dict[str, SearchStrategy[JsonValue]],
    picker: SearchStrategy[set[str]] | None,
) -> JsonValue:
    """Every required key, plus a size-biased pick of the optional ones."""
    # Sorted: set iteration order is not stable across processes, and draw order decides replays.
    chosen = draw(picker) if picker is not None else ()
    return draw(st.fixed_dictionaries({**required, **{key: optional[key] for key in sorted(chosen)}}))


def _collect(
    entries: SearchStrategy[tuple[str, JsonValue]], low: int, high: int
) -> SearchStrategy[dict[str, JsonValue]]:
    def resolved() -> SearchStrategy[dict[str, JsonValue]]:
        if entries.is_empty:
            # No key can be spelled; the floor decides whether that is fatal or only means no extras.
            return st.nothing() if low else st.just({})
        return st.lists(entries, unique_by=lambda entry: entry[0], min_size=low, max_size=high).map(dict)

    # `is_empty` forces the entries, and a self-referential schema is still being built here, so the
    # choice waits for the first draw.
    return st.deferred(resolved)


def _free_names(view: jsonschema_rs.canonical.ObjectView, ctx: StrategyContext) -> SearchStrategy[str]:
    """Names for keys the schema does not spell out."""
    if view.property_names is not None:
        # Python `re` and the validator's engine disagree on several constructs — `\d` is Unicode here
        # and ASCII there — so a drawn name is checked before it becomes a key.
        is_valid = jsonschema_rs.validator_for(view.property_names.to_json_schema()).is_valid
        return from_schema(view.property_names, ctx).map(_key).filter(is_valid)
    # Arbitrary text practically never matches a `patternProperties` regex, so the patterns name keys too.
    sources = [_text(ctx)]
    for pattern in view.pattern_properties:
        compiled = compile_ecma_pattern(pattern)
        if compiled is not None:
            sources.append(st.from_regex(compiled, alphabet=ctx.alphabet.as_strategy()))
    return st.one_of(sources)


def _value_source(
    view: jsonschema_rs.canonical.ObjectView, unnamed: SearchStrategy[JsonValue], ctx: StrategyContext
) -> Callable[[str], SearchStrategy[JsonValue]]:
    """The value strategy a property name answers to under `patternProperties`."""
    # The validator's regex engine decides what a pattern claims — Python `re` would hand the key
    # the wrong schema. Values are built eagerly so a declined schema is refused while the caller
    # can still fall back.
    patterns = [
        _PatternProperty(
            claims=jsonschema_rs.validator_for({"type": "string", "pattern": pattern}).is_valid,
            values=from_schema(schema, ctx),
            schema=schema,
        )
        for pattern, schema in view.pattern_properties.items()
    ]
    cache: dict[tuple[int, ...], SearchStrategy[JsonValue]] = {}

    def value_for(name: str) -> SearchStrategy[JsonValue]:
        matched = tuple(index for index, pattern in enumerate(patterns) if pattern.claims(name))
        strategy = cache.get(matched)
        if strategy is None:
            strategy = cache[matched] = _pattern_values(matched, patterns, unnamed, ctx)
        return strategy

    return value_for


def _pattern_values(
    matched: tuple[int, ...],
    patterns: list[_PatternProperty],
    unnamed: SearchStrategy[JsonValue],
    ctx: StrategyContext,
) -> SearchStrategy[JsonValue]:
    # `additionalProperties` only reaches names no pattern claims.
    if not matched:
        return unnamed
    if len(matched) == 1:
        return patterns[matched[0]].values
    # A name several patterns claim answers to all of them at once.
    return from_schema(_intersection([patterns[index].schema for index in matched]), ctx)


@st.composite  # type: ignore[untyped-decorator]
def _sized_object(
    draw: st.DrawFn,
    required: dict[str, SearchStrategy[JsonValue]],
    optional: dict[str, SearchStrategy[JsonValue]],
    entries: SearchStrategy[tuple[str, JsonValue]] | None,
    minimum: int,
    maximum: int | None,
) -> JsonValue:
    """An object whose property count answers to `minProperties` / `maxProperties`."""
    keys = sorted(optional)
    # Documented keys fill the floor first; names drawn out of nowhere only cover what they cannot.
    low = min(len(keys), max(0, minimum - len(required)))
    high = len(keys) if maximum is None else min(len(keys), maximum - len(required))
    chosen = draw(st.sets(st.sampled_from(keys), min_size=low, max_size=high)) if keys else set()
    result = draw(st.fixed_dictionaries({**required, **{key: optional[key] for key in sorted(chosen)}}))
    if entries is None:
        return result
    # The cap bounds the distance past the floor, not the total — capping the total would pin
    # every object with a floor at or above it to exactly that floor.
    extra_low = max(0, minimum - len(result))
    extra_high = extra_low + _EXTRA_KEYS if maximum is None else min(extra_low + _EXTRA_KEYS, maximum - len(result))
    extra = draw(_collect(entries, extra_low, extra_high))
    return {**extra, **result}


def _key(name: JsonValue) -> str:
    """A drawn property name, checked rather than cast."""
    # `property_names` constrains keys, so canonicalization keeps only what a key could spell.
    assert isinstance(name, str), name
    return name


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
    barred = _barred_divisors(view.not_multiple_of)
    if _every_step_is_barred(multiple_of or Fraction(1), barred):
        return st.nothing()
    if multiple_of is None:
        strategy: SearchStrategy[JsonValue] = st.integers(min_value=view.minimum, max_value=view.maximum)
    else:
        # On a `p/q` grid only multiples of `p` are whole, `q` being coprime to it.
        stride = multiple_of.numerator
        low = None if view.minimum is None else -(-view.minimum // stride)
        high = None if view.maximum is None else view.maximum // stride
        strategy = _steps(low, high).map(lambda step: step * stride)
    return _outside_divisors(strategy, barred)


def _barred_divisors(values: list[Numeric]) -> list[Fraction]:
    # Exact rationals, for the reason `_combined_multiple_of` needs them.
    return [Fraction(str(value)) for value in values]


def _every_step_is_barred(step: Fraction, barred: list[Fraction]) -> bool:
    """Whether the grid the schema admits lands on a barred divisor at every point."""
    return any(step % divisor == 0 for divisor in barred)


def _outside_divisors(strategy: SearchStrategy[JsonValue], barred: list[Fraction]) -> SearchStrategy[JsonValue]:
    if not barred:
        return strategy

    def is_admitted(value: JsonValue) -> bool:
        spelled = Fraction(str(value))
        return all(spelled % divisor != 0 for divisor in barred)

    return strategy.filter(is_admitted)


def _number(view: jsonschema_rs.canonical.NumberView) -> SearchStrategy[JsonValue]:
    barred = _barred_divisors(view.not_multiple_of)
    if view.excludes_integers:
        # Draft 4 spells "not an integer" on the leaf; a barred divisor of one says the same thing.
        barred = [*barred, Fraction(1)]
    multiple_of = _combined_multiple_of(view.multiple_of)
    if multiple_of is not None:
        if _every_step_is_barred(multiple_of, barred):
            return st.nothing()
        steps = _steps(
            _lower_step(view.minimum, view.exclusive_minimum, multiple_of),
            _upper_step(view.maximum, view.exclusive_maximum, multiple_of),
        )
        # Grid points that no float represents round to a neighbour off the grid or outside the bounds.
        is_valid = _grid_check(view, multiple_of)
        strategy = steps.map(lambda step: _fraction_to_json_number(step * multiple_of)).filter(is_valid)
        return _outside_divisors(strategy, barred)

    bounds = _representable_float_bounds(view)
    if bounds is not None:
        float_low, float_high = bounds
        floats = st.floats(min_value=float_low, max_value=float_high, allow_nan=False, allow_infinity=False).map(
            lambda value: value or 0.0
        )
        return _outside_divisors(floats, barred)
    # No float lies within the bounds, leaving the integers that do.
    if _every_step_is_barred(Fraction(1), barred):
        return st.nothing()
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
    strategy = _admitted_strings(view, ctx)
    if view.excluded:
        barred = frozenset(view.excluded)
        strategy = strategy.filter(lambda value: value not in barred)
    if view.excluded_formats:
        # One `anyOf` covers the run: a string is barred as soon as it satisfies any of them.
        matches_barred = make_validator(
            {"anyOf": [{"type": "string", "format": name} for name in view.excluded_formats]},
            _VALIDATOR_BY_CANONICALIZE_DRAFT[ctx.root.draft],
        ).is_valid
        strategy = strategy.filter(lambda value: not matches_barred(value))
    if view.excluded_patterns:
        matches_barred = make_validator(
            {"anyOf": [{"type": "string", "pattern": pattern} for pattern in view.excluded_patterns]},
            _VALIDATOR_BY_CANONICALIZE_DRAFT[ctx.root.draft],
        ).is_valid
        strategy = strategy.filter(lambda value: not matches_barred(value))
    return strategy


# A pattern that is one character class and nothing else, so the characters it bars are the ones it names.
_BARE_CHARACTER_CLASS = re.compile(r"\[\^?(?:[^\\\]]|\\.)+\]")


def _outside_class(pattern: str) -> str | None:
    """A class naming what a bare character class does not, or `None` when the pattern is not one."""
    if _BARE_CHARACTER_CLASS.fullmatch(pattern) is None:
        return None
    return f"[{pattern[2:]}" if pattern.startswith("[^") else f"[^{pattern[1:]}"


def _characters_outside(view: jsonschema_rs.canonical.StringView, ctx: StrategyContext) -> SearchStrategy[str]:
    """Characters no barred pattern names, so drawing does not lean on the filter to choose."""
    characters = ctx.alphabet.as_strategy()
    for pattern in view.excluded_patterns:
        admitted = _outside_class(pattern)
        if admitted is not None:
            # One class can drive the alphabet; anything else the barred run names still filters above.
            return st.from_regex(admitted, fullmatch=True, alphabet=characters)
    return characters


def _admitted_strings(view: jsonschema_rs.canonical.StringView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    if view.min_length is not None and _countable(view.min_length) is None:
        # No string can be that long.
        return st.nothing()
    if view.content_media_types or view.content_encodings:
        return _content_string(view, ctx)
    # A name with no generator behind it is an annotation and leaves the strings unconstrained.
    known = [name for name in view.formats if name in ctx.formats]
    if known:
        # One generator drives, the rest filter; which pair flows depends on the value, so every
        # driver gets a branch and the dead ones reject their draws.
        return st.one_of([_formatted(name, [other for other in known if other != name], view, ctx) for name in known])
    if not view.patterns:
        # Nothing here spells out what the characters have to be, so a value this long can be padded.
        max_length = _countable(view.max_length)
        characters = _characters_outside(view, ctx)
        if view.min_length is not None and view.min_length >= _PADDED_TEXT_THRESHOLD:
            return _padded_text(ctx, view.min_length, max_length, characters)
        kwargs: dict[str, int] = {}
        if view.min_length is not None:
            kwargs["min_size"] = view.min_length
        if max_length is not None:
            kwargs["max_size"] = max_length
        return _text(ctx, characters=characters, **kwargs)
    if view.max_length is not None and any(
        pattern_length_bounds(pattern)[0] > view.max_length for pattern in view.patterns
    ):
        # Every value has to carry a match of each pattern, and one of them cannot fit under the
        # ceiling. Saying so up front spares the caller a filter that never passes.
        return st.nothing()
    drivers = [pattern for pattern in view.patterns if compile_ecma_pattern(pattern) is not None]
    if not drivers:
        # A pattern Python `re` rejects (e.g. ECMA `\p{L}`) can filter but not drive the draw.
        raise UnsupportedView("string")
    return st.one_of(
        [
            _pattern_driven(pattern, [other for other in view.patterns if other != pattern], view, ctx)
            for pattern in drivers
        ]
    )


# Hypothesis indexes every drawn value, and for a string that index is a number with a digit per
# character - work that outgrows the draw itself once a length floor runs into the thousands.
_PADDED_TEXT_THRESHOLD = 256
_PADDED_TEXT_HEAD = 16
# How far past the floor a padded value may run; the floor is the interesting length.
_PADDED_TEXT_SLACK = 8


def _padded_text(
    ctx: StrategyContext, min_length: int, max_length: int | None, characters: SearchStrategy[str]
) -> SearchStrategy[str]:
    """A short head followed by one repeated character, cut to the drawn length."""
    ceiling = min_length + _PADDED_TEXT_SLACK
    if max_length is not None:
        ceiling = min(ceiling, max_length)
    return st.tuples(
        st.integers(min_length, ceiling), _text(ctx, characters=characters, max_size=_PADDED_TEXT_HEAD), characters
    ).map(lambda parts: (parts[1] + parts[2] * parts[0])[: parts[0]])


def _content_string(view: jsonschema_rs.canonical.StringView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    """A decoded value first, with each encoding mapped over it."""
    if "application/json" in view.content_media_types:
        strategy = _anything(ctx).map(json.dumps)
    else:
        strategy = _text(ctx)
    for media_type in view.content_media_types:
        if media_type != "application/json":
            # No builder for this media type; the draft-aware check mirrors what the validator
            # asserts, and passes everything for a type the validator does not know either.
            strategy = strategy.filter(_content_check(ctx, "contentMediaType", media_type))
    for encoding in view.content_encodings:
        if encoding == "base64":
            strategy = strategy.map(_base64_text)
        else:
            strategy = strategy.filter(_content_check(ctx, "contentEncoding", encoding))
    for pattern in view.patterns:
        strategy = strategy.filter(_facet_check("pattern", pattern))
    return _within_length(strategy, view)


def _base64_text(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _content_check(ctx: StrategyContext, keyword: str, value: str) -> Callable[[JsonValue], bool]:
    # Content facets assert only in the drafts where the validator says so; the check has to be
    # built for the document's own draft to agree with it.
    validator_cls = next(cls for cls, draft in CANONICALIZE_DRAFT_BY_VALIDATOR.items() if draft == ctx.root.draft)
    return make_validator({"type": "string", keyword: value}, validator_cls).is_valid


def _pattern_driven(
    pattern: str, others: list[str], view: jsonschema_rs.canonical.StringView, ctx: StrategyContext
) -> SearchStrategy[JsonValue]:
    """Values drawn from one pattern and filtered by the remaining ones."""
    compiled = compile_ecma_pattern(pattern)
    assert compiled is not None
    # A pattern is a search, so the value may carry anything around the match; full matches only
    # would leave `^x` spelling a single string, starving `propertyNames` of distinct keys.
    strategy = st.from_regex(compiled, alphabet=ctx.alphabet.as_strategy())
    try:
        strategy.validate()
    except InvalidArgument:
        # ASCII is the validator's reading, but it leaves characters spelled above that range
        # unreachable; the wider reading draws those, the narrow one still decides what counts.
        strategy = st.from_regex(re.compile(pattern), alphabet=ctx.alphabet.as_strategy()).filter(compiled.search)
    if "$" in pattern:
        # Python also matches `$` before a trailing newline, where the validator means end of string;
        # telling literal `$`s apart costs more than dropping the newline-terminated values.
        strategy = strategy.filter(lambda value: not value.endswith("\n"))
    for other in others:
        strategy = strategy.filter(_facet_check("pattern", other))
    # Length is normally folded into the pattern upstream; this filter is the soundness net.
    return _within_length(strategy, view)


def _formatted(
    name: str, others: list[str], view: jsonschema_rs.canonical.StringView, ctx: StrategyContext
) -> SearchStrategy[JsonValue]:
    """Values from the generator registered for `name`, narrowed to the facets around it."""
    strategy = ctx.formats[name]
    for other in others:
        strategy = strategy.filter(_facet_check("format", other))
    for pattern in view.patterns:
        # A format generator cannot be steered, so the pattern can only be filtered for.
        strategy = strategy.filter(_facet_check("pattern", pattern))
    return _within_length(strategy, view)


# The validator's own engine judges the facet, so `\p{L}` patterns and format assertions filter
# exactly; a name without a checker behind it passes everything, like the validator itself.
@lru_cache(maxsize=512)
def _facet_check(keyword: str, value: str) -> Callable[[JsonValue], bool]:
    return make_validator_for({"type": "string", keyword: value}).is_valid


def _within_length(
    strategy: SearchStrategy[JsonValue], view: jsonschema_rs.canonical.StringView
) -> SearchStrategy[JsonValue]:
    if view.min_length is None and view.max_length is None:
        return strategy
    low = view.min_length or 0
    high = math.inf if view.max_length is None else view.max_length
    return strategy.filter(lambda value: low <= len(value) <= high)


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


def _text(ctx: StrategyContext, *, characters: SearchStrategy[str] | None = None, **kwargs: int) -> SearchStrategy[str]:
    return st.text(alphabet=characters if characters is not None else ctx.alphabet.as_strategy(), **kwargs)


# What each JSON type name admits. `True == 1` in Python, but `true` is not a JSON number, and a
# `TypedGroupView` over `integer` comes from Draft 4, where a fractional spelling is not one either.
_TYPE_CHECKS: dict[str, Callable[[JsonValue], bool]] = {
    "null": lambda value: value is None,
    "boolean": lambda value: isinstance(value, bool),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "string": lambda value: isinstance(value, str),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
}


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
