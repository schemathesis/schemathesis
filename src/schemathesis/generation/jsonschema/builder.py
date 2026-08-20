from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import jsonschema_rs
from hypothesis import strategies as st
from jsonschema_rs import canonical

from schemathesis.config import OutputConfig
from schemathesis.core.cache import MISSING
from schemathesis.core.errors import (
    InvalidRegexPattern,
    InvalidSchema,
    RejectedSchemaDefinition,
    UnsupportedSchema,
    is_regex_validation_error,
)
from schemathesis.core.jsonschema import FANCY_REGEX_OPTIONS
from schemathesis.generation._cache import schema_cache_key
from schemathesis.generation.hypothesis import canonical_form_cache, canonical_strategy_cache
from schemathesis.generation.jsonschema.context import Alphabet, StrategyContext
from schemathesis.generation.jsonschema.strategy import _displayed, from_schema

if TYPE_CHECKING:
    from collections.abc import Iterator

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
) -> SearchStrategy[JsonValue]:
    """Values the schema admits; `UnsupportedSchema` when this engine does not fully model it."""
    alphabet = alphabet if alphabet is not None else Alphabet()
    try:
        schema_key = schema_cache_key(schema)
    except (TypeError, ValueError):
        schema_key = None
    # Negative generation reaches this from a `flatmap`, so the same mutated schema comes back on
    # every draw; rebuilding its strategy each time is what the cache is for.
    key = (schema_key, draft, id(formats), alphabet) if schema_key is not None else None
    if key is not None:
        cached = canonical_strategy_cache.get(key)
        if cached is not MISSING:
            return cached[1]
    strategy = _build(schema, draft=draft, formats=formats, alphabet=alphabet, schema_key=schema_key)
    if key is not None:
        # Keeping `formats` alive next to the strategy stops its `id` from being recycled
        # into a stale hit once the caller drops it.
        canonical_strategy_cache[key] = (formats, strategy)
    return strategy


def _build(
    schema: JsonSchema,
    *,
    draft: int,
    formats: dict[str, SearchStrategy],
    alphabet: Alphabet,
    schema_key: tuple[str, ...] | None = None,
) -> SearchStrategy[JsonValue]:
    # The canonical form answers to the schema and draft alone, so a differing alphabet or format map reuses it.
    canonical_key = (schema_key, draft) if schema_key is not None else None
    cached_form = canonical_form_cache.get(canonical_key) if canonical_key is not None else MISSING
    canonical_schema: jsonschema_rs.CanonicalSchema
    if cached_form is not MISSING:
        canonical_schema = cached_form
    else:
        with _reported():
            canonical_schema = jsonschema_rs.canonicalize(
                schema,
                draft=draft,
                pattern_options=FANCY_REGEX_OPTIONS,
                # Draft 2020-12 treats `format` as an annotation and drops it, which would leave a
                # `format`-carrying schema generating arbitrary strings on this path.
                validate_formats=True,
            )
        if canonical_key is not None:
            canonical_form_cache[canonical_key] = canonical_schema
    if canonical_schema.kind is canonical.CanonicalKind.RAW:
        raise UnsupportedSchema.from_reason(f"this part of it:\n\n{_displayed(canonical_schema)}")
    # Spelled out so the caller reports an unsatisfiable schema; no other engine gets a say.
    if canonical_schema.satisfiability() is canonical.Satisfiability.NO:
        return EMPTY_STRATEGY
    context = StrategyContext(root=canonical_schema, alphabet=alphabet, formats=formats)
    # Folding an `allOf` canonicalizes again, so a rejected schema and both spellings of
    # "not modeled here" can arrive from this block too.
    with _reported():
        strategy = from_schema(canonical_schema, context)
        if not context.cyclic:
            return strategy
        # What a cycle leads back to is spelled lazily, so this is where the rest of it gets built -
        # and where a node this module cannot take still gets to route the whole schema elsewhere.
        # A cycle can also leave a schema whose every value would have to be infinitely deep. The
        # strategies form the same cycle, so Hypothesis works that out here.
        empty = strategy.is_empty
    # Spelling it out keeps the caller reporting an unsatisfiable schema, where a strategy that
    # only fails on a draw would surface as whatever Hypothesis raises first.
    return EMPTY_STRATEGY if empty else strategy


@contextmanager
def _reported() -> Iterator[None]:
    """Canonicalization failures, as the errors reported against the operation."""
    try:
        yield
    except jsonschema_rs.ValidationError as exc:
        raise _schema_error(exc) from None
    except jsonschema_rs.canonical.InvalidPattern as exc:
        raise InvalidRegexPattern(f"Failed to generate test cases for this API operation: {exc}") from None
    except jsonschema_rs.canonical.CanonicalizationError as exc:
        raise InvalidSchema(f"Failed to generate test cases for this API operation: {exc}") from None


def _schema_error(error: jsonschema_rs.ValidationError) -> InvalidSchema:
    """A schema its own draft rejects, as the error to report against the operation."""
    if is_regex_validation_error(error):
        return InvalidRegexPattern.from_jsonschema_rs_error(error)
    return RejectedSchemaDefinition.from_jsonschema_error(error, path=None, method=None, config=OutputConfig())
