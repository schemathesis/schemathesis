from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from schemathesis.core.parameters import ParameterLocation


class Cardinality(str, enum.Enum):
    """Whether a response contains one resource or many resources."""

    ONE = "ONE"
    MANY = "MANY"


class _ResourceRef(Protocol):
    """Identifies a resource by name."""

    @property
    def name(self) -> str: ...  # pragma: no cover


class ResourceFieldRef(Protocol):
    """One ``(resource, field)`` reference inside a generated request.

    Implemented structurally by spec-specific types (e.g. OpenAPI's ``InputSlot``).
    """

    @property
    def resource(self) -> _ResourceRef: ...  # pragma: no cover
    @property
    def resource_field(self) -> str | None: ...  # pragma: no cover
    @property
    def parameter_name(self) -> str | int: ...  # pragma: no cover
    @property
    def parameter_location(self) -> ParameterLocation: ...  # pragma: no cover


@dataclass(slots=True)
class ResourceDescriptor:
    """Describes how to capture resources from an operation response.

    Attributes:
        resource_name: Type of resource (e.g., "User", "Product")
        operation: Operation label that produces this resource (e.g., "POST /users")
        status_code: HTTP status code to match
        pointer: JSON pointer to resource location in response (empty for root)
        cardinality: Whether response contains ONE resource or MANY
        is_primitive_identifier: True when response is a primitive value that IS the identifier
        identifier_field: Field name to use when wrapping primitive identifiers (e.g., "slug")

    """

    resource_name: str
    operation: str
    status_code: str
    pointer: str
    cardinality: Cardinality
    is_primitive_identifier: bool = False
    identifier_field: str | None = None
    # When True, the response payload is a `{<id>: <object>, ...}` map and the keys are the
    # identifier values (e.g. Kubernetes pod-statuses, TBA team-statuses, slack auth.test).
    extract_object_keys: bool = False
