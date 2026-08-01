from __future__ import annotations

from typing import TYPE_CHECKING

import jsonschema_rs
from hypothesis import strategies as st

from schemathesis.core.jsonschema import FANCY_REGEX_OPTIONS
from schemathesis.generation.jsonschema.context import Alphabet, StrategyContext
from schemathesis.generation.jsonschema.strategy import UnsupportedView, from_schema

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy

    from schemathesis.core.jsonschema.types import JsonSchema, JsonValue


# What this module returns for a schema that admits no value. Callers recognize it by identity, which
# says "this engine ruled the schema out" without forcing an unrelated strategy to build itself first.
EMPTY_STRATEGY: SearchStrategy = st.nothing()


def build(
    schema: JsonSchema,
    *,
    draft: int,
    formats: dict[str, SearchStrategy],
    alphabet: Alphabet | None = None,
) -> SearchStrategy[JsonValue] | None:
    """Values the schema admits, or `None` for a schema this engine does not fully model."""
    try:
        canonical_schema = jsonschema_rs.canonicalize(
            schema,
            draft=draft,
            pattern_options=FANCY_REGEX_OPTIONS,
            # Draft 2020-12 treats `format` as an annotation and drops it, which would leave a
            # `format`-carrying schema generating arbitrary strings on this path.
            validate_formats=True,
        )
    except (jsonschema_rs.ValidationError, jsonschema_rs.canonical.CanonicalizationError):
        return None
    if canonical_schema.kind == "raw":
        return None
    # Spelled out so the caller reports an unsatisfiable schema; no other engine gets a say.
    if not canonical_schema.is_satisfiable():
        return EMPTY_STRATEGY
    context = StrategyContext(
        root=canonical_schema,
        alphabet=alphabet if alphabet is not None else Alphabet(),
        formats=formats,
    )
    try:
        strategy = from_schema(canonical_schema, context)
        if not context.cyclic:
            return strategy
        # What a cycle leads back to is spelled lazily, so this is where the rest of it gets built -
        # and where a node this module cannot take still gets to route the whole schema elsewhere.
        # A cycle can also leave a schema whose every value would have to be infinitely deep. The
        # strategies form the same cycle, so Hypothesis works that out here.
        empty = strategy.is_empty
    # Folding an `allOf` canonicalizes again, so this covers both spellings of "not modeled here".
    except (jsonschema_rs.ValidationError, jsonschema_rs.canonical.CanonicalizationError, UnsupportedView):
        return None
    # Spelling it out keeps the caller reporting an unsatisfiable schema, where a strategy that
    # only fails on a draw would surface as whatever Hypothesis raises first.
    return EMPTY_STRATEGY if empty else strategy
