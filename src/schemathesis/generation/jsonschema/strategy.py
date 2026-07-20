from __future__ import annotations

from typing import TYPE_CHECKING, cast

import jsonschema_rs
from hypothesis import strategies as st

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy

    from schemathesis.core.jsonschema.types import JsonValue
    from schemathesis.generation.jsonschema.context import StrategyContext


class UnsupportedView(Exception):
    """A canonical view without a lifter here; newer `jsonschema-rs` releases may add view classes."""


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
    raise UnsupportedView(schema.kind)


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


def _text(ctx: StrategyContext) -> SearchStrategy[str]:
    codec = ctx.alphabet.codec
    if codec is not None:
        alphabet = st.characters(codec=codec, exclude_characters="" if ctx.alphabet.allow_x00 else "\x00")
    else:
        alphabet = st.characters(exclude_characters="" if ctx.alphabet.allow_x00 else "\x00")
    return st.text(alphabet=alphabet)


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
