from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    import jsonschema_rs
    from hypothesis.strategies import SearchStrategy

    from schemathesis.generation.jsonschema.strategy import _Node


@dataclass(slots=True, frozen=True)
class Alphabet:
    """Character-set control for generated strings and property names."""

    allow_x00: bool = True
    codec: str | None = "utf-8"
    max_codepoint: int | None = None
    exclude_characters: str = ""
    # Whether a drawn string may open or close with whitespace; header values cannot. Applied as a filter on each leaf.
    allow_surrounding_whitespace: bool = True
    # Shortest name a drawn key may carry; header names cannot be empty.
    min_name_length: int = 0
    # Characters a drawn key may carry, where a key answers to a narrower grammar than a value.
    name_characters: str | None = None

    def as_strategy(self) -> SearchStrategy[str]:
        return _characters(self.allow_x00, self.codec, self.max_codepoint, self.exclude_characters)

    def names_as_strategy(self) -> SearchStrategy[str]:
        if self.name_characters is None:
            return self.as_strategy()
        return _sampled(self.name_characters)


@lru_cache
def _sampled(characters: str) -> SearchStrategy[str]:
    from hypothesis import strategies as st

    return st.sampled_from(characters)


def _characters(
    allow_x00: bool, codec: str | None, max_codepoint: int | None, exclude_characters: str
) -> SearchStrategy[str]:
    from hypothesis import strategies as st

    if not allow_x00 and "\x00" not in exclude_characters:
        exclude_characters += "\x00"
    if codec is None:
        # Lone surrogates cannot be encoded for the wire.
        return st.characters(
            max_codepoint=max_codepoint, exclude_characters=exclude_characters, exclude_categories=["Cs"]
        )
    return st.characters(codec=codec, max_codepoint=max_codepoint, exclude_characters=exclude_characters)


@dataclass(frozen=True, slots=True)
class FormatLengths:
    """What the caller knows about the lengths one format generator reaches."""

    shortest: int
    longest: int
    # Values inside a narrower window, where the generator can be pointed at one.
    within: Callable[[int, float], SearchStrategy[str] | None] | None = None


@dataclass(slots=True)
class StrategyContext:
    """Shared configuration threaded through `from_schema`."""

    # The document `#` names; it is not one of its own definitions.
    root: jsonschema_rs.CanonicalSchema
    alphabet: Alphabet = field(default_factory=Alphabet)
    # Values for `format`, by name. Names absent here are annotations and do not constrain generation.
    formats: dict[str, SearchStrategy] = field(default_factory=dict)
    # What each generator above reaches, where the caller knows it.
    format_lengths: dict[str, FormatLengths] = field(default_factory=dict)
    cache: dict[jsonschema_rs.CanonicalSchema, _Node] = field(default_factory=dict)
    # Placeholders for the pointer targets being built, by URI.
    pending: dict[str, SearchStrategy] = field(default_factory=dict)
    # Whether a pointer led back into a value still being built.
    cyclic: bool = False
    # Pointer targets followed while folding an `allOf`, so a cyclic one stops unrolling.
    following: set[str] = field(default_factory=set)
    # Placeholders for the `allOf` intersections being built, so one met again stops there.
    folding: dict[jsonschema_rs.CanonicalSchema, SearchStrategy] = field(default_factory=dict)
    # Schemas whose complement is being built, so a bar reaching back is spotted.
    complementing: set[jsonschema_rs.CanonicalSchema] = field(default_factory=set)
    # Whether an integer may be drawn spelled as a float, which only a JSON carrier keeps apart.
    whole_floats: bool = False

    @property
    def building_cycle(self) -> bool:
        """Whether a placeholder stands for something still being built, which `is_empty` would force too early."""
        return bool(self.pending or self.folding)
