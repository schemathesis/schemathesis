from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from schemathesis.transport.serialization import Binary

if TYPE_CHECKING:
    from schemathesis.core.jsonschema.types import JsonValue
    from schemathesis.core.mutations import MutationMetadata
    from schemathesis.generation.dictionaries import DictionaryDraw
    from schemathesis.python._constants.pool import ConstantDraw
    from schemathesis.resources import PoolDraw, SemanticDraw


@dataclass(slots=True)
class GeneratedValue:
    """Wrapper for a generated value plus optional generation-time metadata."""

    value: Any
    meta: MutationMetadata | None
    pool_draws: tuple[PoolDraw, ...] = ()
    semantic_draws: tuple[SemanticDraw, ...] = ()
    dictionary_draws: tuple[DictionaryDraw, ...] = ()
    constants_draws: tuple[ConstantDraw, ...] = ()

    def map_value(self, function: Callable[[Any], Any]) -> GeneratedValue:
        """Apply `function` to the value, keeping all provenance."""
        return GeneratedValue(
            function(self.value),
            self.meta,
            self.pool_draws,
            self.semantic_draws,
            self.dictionary_draws,
            self.constants_draws,
        )


MISSING: object = object()


def get_at_path(target: object, path: tuple[str, ...]) -> object:
    """Read the value at path. Returns the ``MISSING`` sentinel when any segment is absent."""
    cursor: object = target
    for segment in path:
        if not isinstance(cursor, dict) or segment not in cursor:
            return MISSING
        cursor = cursor[segment]
    return cursor


def prune_overwritten_constants(
    constants_draws: tuple[ConstantDraw, ...], value: JsonValue
) -> tuple[ConstantDraw, ...]:
    """Drop provenance for constant leaves a later overlay overwrote, so draws match the emitted value."""
    if not constants_draws:
        return constants_draws
    return tuple(draw for draw in constants_draws if constant_value_at_draw(value, draw) == draw.value)


def prune_overwritten_body_constants(
    constants_draws: tuple[ConstantDraw, ...], body: JsonValue
) -> tuple[ConstantDraw, ...]:
    """Prune only body-location draws against `body`; other locations aren't part of the body."""
    if not constants_draws:
        return constants_draws
    return tuple(
        draw for draw in constants_draws if draw.location != "body" or constant_value_at_draw(body, draw) == draw.value
    )


def constant_value_at_draw(value: object, draw: ConstantDraw) -> object:
    path = (
        tuple(segment for segment in draw.body_path.split("/") if segment) if draw.body_path else (draw.parameter_name,)
    )
    current = get_at_path(value, path)
    return current.data if isinstance(current, Binary) else current
