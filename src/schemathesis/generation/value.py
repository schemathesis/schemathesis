from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from schemathesis.generation.dictionaries import DictionaryDraw
    from schemathesis.resources import PoolDraw, SemanticDraw
    from schemathesis.specs.openapi.negative.mutations import MutationMetadata


@dataclass(slots=True)
class GeneratedValue:
    """Wrapper for a generated value plus optional generation-time metadata."""

    value: Any
    meta: MutationMetadata | None
    pool_draws: tuple[PoolDraw, ...] = ()
    semantic_draws: tuple[SemanticDraw, ...] = ()
    dictionary_draws: tuple[DictionaryDraw, ...] = ()
