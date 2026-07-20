from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import jsonschema_rs
    from hypothesis.strategies import SearchStrategy


@dataclass(slots=True)
class Alphabet:
    """Character-set control for generated strings and property names."""

    allow_x00: bool = True
    codec: str | None = "utf-8"


@dataclass(slots=True)
class StrategyContext:
    """Shared configuration threaded through `from_schema`."""

    alphabet: Alphabet = field(default_factory=Alphabet)
    cache: dict[jsonschema_rs.CanonicalSchema, SearchStrategy] = field(default_factory=dict)
