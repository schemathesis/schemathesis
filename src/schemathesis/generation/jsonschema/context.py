from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from schemathesis.generation.jsonschema.formats import FormatRegistry

if TYPE_CHECKING:
    import jsonschema_rs
    from hypothesis.strategies import SearchStrategy


@dataclass
class Alphabet:
    """Character-set control for generated strings and property names."""

    allow_x00: bool = True
    codec: str | None = "utf-8"

    def check_name_allowed(self, name: str) -> bool:
        if not self.allow_x00 and "\x00" in name:
            return False
        if self.codec is not None:
            try:
                name.encode(self.codec)
            except UnicodeEncodeError:
                return False
        return True


@dataclass
class StrategyContext:
    """Shared configuration threaded through `from_schema`."""

    formats: FormatRegistry = field(default_factory=FormatRegistry)
    alphabet: Alphabet = field(default_factory=Alphabet)
    # Content-addressed strategy memo, keyed by canonical-schema identity (Rust content hash).
    cache: dict[jsonschema_rs.CanonicalSchema, SearchStrategy] = field(default_factory=dict)
    # `not` schemas currently being expanded via `negate`; a repeat means the complement has no
    # positive form (e.g. non-integer number, string not matching a pattern) -> fall back to filtering.
    expanding: set[jsonschema_rs.CanonicalSchema] = field(default_factory=set)
    # Symbolic-ref target graph (uri -> canonical target), captured once from the root schema.
    definitions: dict[str, jsonschema_rs.CanonicalSchema] | None = None
    # uri -> deferred strategy, so every use of a ref shares one binding (handles recursion).
    references: dict[str, SearchStrategy] = field(default_factory=dict)
    # Cap on how deep a single uri may recurse within one draw; bounds generated structure size.
    max_recursion_depth: int = 4
    # Per-uri active recursion depth during a draw (restored as the draw unwinds).
    depths: dict[str, int] = field(default_factory=dict)
