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
    # Identity whose request produced the resource; `None` outside multi-identity auth.
    source_identity: str | None = None
    # The value was a parent path parameter of the source request, so that request referenced it, not created it.
    source_input: bool = False
    # Query of the source request, so a list it came from can be requested again as sent.
    source_query: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class SemanticDraw:
    """Provenance for a single semantic value index draw consumed by a generated case."""

    path: tuple[str, ...]
    type_token: str
    format_token: str | None
    pattern_hash: str | None
    normalized_name: str
    value: str | int | float
    source_operation: str


@dataclass(slots=True, frozen=True)
class PoolPick:
    """Correlated values and their provenance from `pick_correlated_values`.

    `misses` lists resource-bound `(location, parameter_name)` slots with no captured value, for the chain rate.
    """

    values: dict[tuple[ParameterLocation, str], Any] = field(default_factory=dict)
    draws: tuple[PoolDraw, ...] = ()
    misses: tuple[tuple[str, str], ...] = ()


class ResourcePool(Protocol):
    """Draw side: hands out values captured from earlier responses."""

    def pick_correlated_values(
        self,
        *,
        operation: APIOperation,
    ) -> PoolPick:
        """Correlated pool values for every resource-bound slot of `operation`, with provenance."""
        ...  # pragma: no cover

    def deletes_existing_data(self, *, operation: APIOperation, case: Case) -> bool:
        """Whether `case` deletes a resource the API had before the run."""
        ...  # pragma: no cover


class ResourceRecorder(Protocol):
    """Record side: captures values from responses and requests as they happen."""

    def should_record(self, *, operation: str) -> bool: ...  # pragma: no cover

    def should_record_request(self, *, operation: str) -> bool: ...  # pragma: no cover

    def record_response(
        self,
        *,
        operation: APIOperation,
        response: Response,
        case: Case,
    ) -> None: ...  # pragma: no cover

    def record_origin(self, *, operation: APIOperation, response: Response, case: Case) -> None:
        """Note which returned resources the API already had, without offering their values to later draws."""
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
    ) -> None: ...  # pragma: no cover


class ExtraDataSource(ResourcePool, ResourceRecorder, Protocol):
    """Provides extra data from captured API responses for test generation."""


def record_interaction(source: ResourceRecorder, case: Case, response: Response) -> None:
    """Capture the response and the request inputs of one call, where the source records them."""
    operation = case.operation
    if source.should_record(operation=operation.label):
        source.record_response(operation=operation, response=response, case=case)
    if source.should_record_request(operation=operation.label):
        source.record_request(operation=operation, case=case, status_code=response.status_code)


def record_delete_attempt(source: ResourceRecorder, case: Case, status_code: int) -> None:
    """Record a DELETE that succeeded, so later draws stop reusing the deleted resource."""
    if 200 <= status_code < 300:
        source.record_successful_delete(operation=case.operation, case=case)


__all__ = [
    "Cardinality",
    "ExtraDataSource",
    "ResourcePool",
    "ResourceRecorder",
    "PoolDraw",
    "PoolPick",
    "ResourceDescriptor",
    "ResourceInstance",
    "ResourceRepository",
    "SemanticDraw",
]
