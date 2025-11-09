from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from schemathesis.core.parameters import ParameterLocation

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation


class ParameterSchemaAugmenter(Protocol):
    """Augments parameter schemas with runtime information (e.g., captured resources)."""

    def augment(
        self,
        *,
        operation: APIOperation,
        location: ParameterLocation,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a schema enriched for the given operation & location."""
        ...
