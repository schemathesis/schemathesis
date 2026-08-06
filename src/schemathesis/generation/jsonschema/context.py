from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import jsonschema_rs
    from hypothesis.strategies import SearchStrategy


class Distinctness(str, Enum):
    """Whether an array's elements may coincide."""

    UNCONSTRAINED = "unconstrained"
    ALL_DISTINCT = "all_distinct"
    SOME_REPEATED = "some_repeated"


@dataclass(slots=True, frozen=True)
class Alphabet:
    """Character-set control for generated strings and property names."""

    allow_x00: bool = True
    codec: str | None = "utf-8"
    max_codepoint: int | None = None
    exclude_characters: str = ""
    # Whether a drawn string may open with whitespace. Header values cannot, and that is a property
    # of the value rather than of its characters, so it rides along here as a filter on each leaf.
    allow_leading_whitespace: bool = True

    def as_strategy(self) -> SearchStrategy[str]:
        return _characters(self.allow_x00, self.codec, self.max_codepoint, self.exclude_characters)


@lru_cache
def _characters(
    allow_x00: bool, codec: str | None, max_codepoint: int | None, exclude_characters: str
) -> SearchStrategy[str]:
    from hypothesis import strategies as st

    if not allow_x00 and "\x00" not in exclude_characters:
        exclude_characters += "\x00"
    if codec is None:
        return st.characters(max_codepoint=max_codepoint, exclude_characters=exclude_characters)
    return st.characters(codec=codec, max_codepoint=max_codepoint, exclude_characters=exclude_characters)


@dataclass(slots=True)
class StrategyContext:
    """Shared configuration threaded through `from_schema`."""

    # The document `#` names; it is not one of its own definitions.
    root: jsonschema_rs.CanonicalSchema
    alphabet: Alphabet = field(default_factory=Alphabet)
    # Values for `format`, by name. Names absent here are annotations and do not constrain generation.
    formats: dict[str, SearchStrategy] = field(default_factory=dict)
    cache: dict[jsonschema_rs.CanonicalSchema, SearchStrategy] = field(default_factory=dict)
    # Placeholders for the pointer targets currently being built, by URI.
    pending: dict[str, SearchStrategy] = field(default_factory=dict)
    # Whether a pointer led back into a value still being built.
    cyclic: bool = False
    # Pointer targets already followed while folding an `allOf`, which stops a cyclic one from
    # unrolling forever.
    following: set[str] = field(default_factory=set)
    # Schemas whose complement is currently being built, so a bar reaching back is spotted.
    complementing: set[jsonschema_rs.CanonicalSchema] = field(default_factory=set)
