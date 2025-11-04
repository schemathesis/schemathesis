from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.core.parameters import ParameterLocation
from schemathesis.resources.descriptors import Cardinality, ResourceDescriptor
from schemathesis.resources.repository import ResourceInstance, ResourceRepository

if TYPE_CHECKING:
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation


class ExtraDataSource(Protocol):
    """Provides extra data to augment parameter schemas for test generation."""

    def augment(
        self,
        *,
        operation: APIOperation,
        location: ParameterLocation,
        schema: JsonSchema,
    ) -> JsonSchema:
        """Return a schema augmented for the given operation & location."""
        ...  # pragma: no cover

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
        """Record a response for later use in test generation.

        Handles deserialization and extraction of response data internally.

        Args:
            operation: The API operation that was tested.
            response: The response object to record.
            case: The test case that generated this response.

        """
        ...  # pragma: no cover


__all__ = [
    "Cardinality",
    "ExtraDataSource",
    "ResourceDescriptor",
    "ResourceInstance",
    "ResourceRepository",
]
