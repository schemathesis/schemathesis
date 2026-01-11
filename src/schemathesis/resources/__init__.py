from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from schemathesis.resources.descriptors import Cardinality, ResourceDescriptor
from schemathesis.resources.repository import ResourceInstance, ResourceRepository

if TYPE_CHECKING:
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation


class ExtraDataSource(Protocol):
    """Provides extra data from captured API responses for test generation."""

    def should_record(self, *, operation: str) -> bool:
        """Check if responses should be recorded for this operation."""
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
    "ResourceDescriptor",
    "ResourceInstance",
    "ResourceRepository",
]
