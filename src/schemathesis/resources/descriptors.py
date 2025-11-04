from __future__ import annotations

import enum
from dataclasses import dataclass


class Cardinality(str, enum.Enum):
    """Whether a response contains one resource or many resources."""

    ONE = "ONE"
    MANY = "MANY"


@dataclass
class ResourceDescriptor:
    """Metadata describing how to capture resources from operation responses.

    This is a generic descriptor that can be used by any spec implementation.
    Spec-specific builders (e.g., OpenAPI) create these descriptors from their
    schema analysis.

    Attributes:
        resource_name: Type of resource (e.g., "User", "Product")
        operation_label: Operation that produces this resource (e.g., "POST /users")
        status_code: HTTP status code to match
        pointer: JSON pointer to resource location in response (empty for root)
        cardinality: Whether response contains ONE resource or MANY
        fields: Resource field names (currently unused, reserved for future validation)

    """

    __slots__ = ("resource_name", "operation_label", "status_code", "pointer", "cardinality", "fields")

    resource_name: str
    operation_label: str
    status_code: int
    pointer: str
    cardinality: Cardinality
    fields: tuple[str, ...]
