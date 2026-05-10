from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from schemathesis.resources.descriptors import Cardinality, ResourceDescriptor
from schemathesis.resources.repository import ResourceInstance, ResourceRepository

if TYPE_CHECKING:
    from schemathesis.core.parameters import ParameterLocation
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation


@dataclass(slots=True, frozen=True)
class PoolDraw:
    """Provenance for a single resource pool draw consumed by a generated case."""

    location: str
    parameter_name: str
    resource_name: str
    resource_field: str
    source_operation: str
    source_status: int


@dataclass(slots=True, frozen=True)
class PoolPick:
    """Bundle of correlated values + their provenance returned from `pick_correlated_values`.

    `misses` records resource-bound (location, parameter_name) slots that the engine wanted to
    fill from the pool but couldn't (no captured instance available). Useful for the analyzer
    to compute "chain rate" — successful draws over (draws + misses).
    """

    values: dict[tuple[ParameterLocation, str], Any] = field(default_factory=dict)
    draws: tuple[PoolDraw, ...] = ()
    misses: tuple[tuple[str, str], ...] = ()


class ExtraDataSource(Protocol):
    """Provides extra data from captured API responses for test generation."""

    def should_record(self, *, operation: str) -> bool:
        """Check if responses should be recorded for this operation."""
        ...  # pragma: no cover

    def should_record_request(self, *, operation: str) -> bool:
        """Check if request inputs should be captured for this operation."""
        ...  # pragma: no cover

    def pick_captured_value(
        self,
        *,
        operation: APIOperation,
        location: ParameterLocation,
        name: str,
    ) -> Any | None:
        """Return one weighted-selected pool value for a resource-bound parameter."""
        ...  # pragma: no cover

    def pick_correlated_values(
        self,
        *,
        operation: APIOperation,
    ) -> PoolPick:
        """Return correlated pool values for all resource-bound slots in one operation, with provenance."""
        ...  # pragma: no cover

    def record_response(
        self,
        *,
        operation: APIOperation,
        response: Response,
        case: Case,
    ) -> None:
        """Record a response for later use in test generation."""
        ...  # pragma: no cover

    def record_request(
        self,
        *,
        operation: APIOperation,
        case: Case,
        status_code: int,
    ) -> None:
        """Capture request inputs from a successful call."""
        ...  # pragma: no cover

    def record_successful_delete(
        self,
        *,
        operation: APIOperation,
        case: Case,
    ) -> None:
        """Record that a resource was successfully deleted."""
        ...  # pragma: no cover


__all__ = [
    "Cardinality",
    "ExtraDataSource",
    "PoolDraw",
    "PoolPick",
    "ResourceDescriptor",
    "ResourceInstance",
    "ResourceRepository",
]
