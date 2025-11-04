from __future__ import annotations

import enum
from dataclasses import dataclass


class Cardinality(str, enum.Enum):
    """Whether a response contains one resource or many resources."""

    ONE = "ONE"
    MANY = "MANY"


@dataclass(slots=True)
class ResourceDescriptor:
    """Describes how to capture resources from an operation response.

    Attributes:
        resource_name: Type of resource (e.g., "User", "Product")
        operation: Operation label that produces this resource (e.g., "POST /users")
        status_code: HTTP status code to match
        pointer: JSON pointer to resource location in response (empty for root)
        cardinality: Whether response contains ONE resource or MANY

    """

    resource_name: str
    operation: str
    status_code: str
    pointer: str
    cardinality: Cardinality
