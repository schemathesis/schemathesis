from __future__ import annotations

import math
import re
from functools import lru_cache
from typing import TYPE_CHECKING, cast

import jsonschema_rs
from hypothesis import strategies as st

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy

    from schemathesis.core.jsonschema.types import JsonValue
    from schemathesis.generation.jsonschema.context import StrategyContext


class UnsupportedView(Exception):
    """A canonical node this module cannot build from; the caller falls back to `hypothesis-jsonschema`."""


def from_schema(schema: jsonschema_rs.CanonicalSchema, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    cached = ctx.cache.get(schema)
    if cached is None:
        cached = _build(schema, ctx)
        ctx.cache[schema] = cached
    return cached


def _build(schema: jsonschema_rs.CanonicalSchema, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
    canon = jsonschema_rs.canonical
    view = schema.view()
    if isinstance(view, canon.TrueView):
        return _anything(ctx)
    if isinstance(view, canon.FalseView):
        return st.nothing()
    if isinstance(view, canon.ConstView):
        return st.just(cast("JsonValue", view.value))
    if isinstance(view, canon.EnumView):
        return st.sampled_from(view.values)
    if isinstance(view, canon.MultiTypeView):
        return st.one_of([_bare_type(name, ctx) for name in view.types])
    if isinstance(view, canon.TypedGroupView):
        return from_schema(view.body, ctx)
    if isinstance(view, canon.AnyOfView):
        return st.one_of([from_schema(branch, ctx) for branch in view.branches])
    if isinstance(view, canon.IntegerView):
        return st.integers(min_value=view.minimum, max_value=view.maximum)
    if isinstance(view, canon.StringView):
        return _string(view, ctx)
    raise UnsupportedView(schema.kind)


def _string(view: jsonschema_rs.canonical.StringView, ctx: StrategyContext) -> SearchStrategy[JsonValue]:
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
    strategy = st.from_regex(view.patterns[0], fullmatch=True, alphabet=_alphabet(ctx))
    if view.min_length is not None or view.max_length is not None:
        # Length is normally folded into the pattern upstream; this filter is the soundness net.
        low = view.min_length or 0
        high = math.inf if view.max_length is None else view.max_length
        strategy = strategy.filter(lambda value: low <= len(value) <= high)
    return strategy


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
    text = st.text(alphabet=_alphabet_for(allow_x00, codec))
    return st.recursive(
        st.none()
        | st.booleans()
        | st.integers()
        | st.floats(allow_nan=False, allow_infinity=False).map(lambda x: x or 0.0)
        | text,
        lambda children: st.lists(children, max_size=3) | st.dictionaries(text, children, max_size=3),
    )


def _text(ctx: StrategyContext, **kwargs: int) -> SearchStrategy[str]:
    return st.text(alphabet=_alphabet(ctx), **kwargs)


def _alphabet(ctx: StrategyContext) -> SearchStrategy[str]:
    return _alphabet_for(ctx.alphabet.allow_x00, ctx.alphabet.codec)


@lru_cache
def _alphabet_for(allow_x00: bool, codec: str | None) -> SearchStrategy[str]:
    exclude_characters = "" if allow_x00 else "\x00"
    if codec is not None:
        return st.characters(codec=codec, exclude_characters=exclude_characters)
    return st.characters(exclude_characters=exclude_characters)


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
