from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import jsonschema_rs
    from hypothesis.strategies import SearchStrategy


@dataclass(slots=True)
class Alphabet:
    """Character-set control for generated strings and property names."""

    allow_x00: bool = True
    codec: str | None = "utf-8"

    def as_strategy(self) -> SearchStrategy[str]:
        return _characters(self.allow_x00, self.codec)


@lru_cache
def _characters(allow_x00: bool, codec: str | None) -> SearchStrategy[str]:
    from hypothesis import strategies as st

    exclude_characters = "" if allow_x00 else "\x00"
    if codec is not None:
        return st.characters(codec=codec, exclude_characters=exclude_characters)
    return st.characters(exclude_characters=exclude_characters)


@dataclass(slots=True)
class StrategyContext:
    """Shared configuration threaded through `from_schema`."""

    # The document `#` names; it is not one of its own definitions.
    root: jsonschema_rs.CanonicalSchema
    alphabet: Alphabet = field(default_factory=Alphabet)
    # Values for `format`, by name. Names absent here are annotations and do not constrain generation.
    formats: dict[str, SearchStrategy] = field(default_factory=dict)
    cache: dict[jsonschema_rs.CanonicalSchema, SearchStrategy] = field(default_factory=dict)
    # Reference targets currently being built, so a cycle can be spelled lazily instead of unrolled.
    resolving: set[str] = field(default_factory=set)
