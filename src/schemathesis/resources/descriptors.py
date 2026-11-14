from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from schemathesis.core.parameters import ParameterLocation


class Cardinality(enum.StrEnum):
    """Whether a response contains one resource or many resources."""

    ONE = "ONE"
    MANY = "MANY"


class _ResourceRef(Protocol):
    @property
    def name(self) -> str: ...  # pragma: no cover


class ResourceFieldRef(Protocol):
    """A `(resource, field)` reference inside a generated request, e.g. OpenAPI's `InputSlot`."""

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
    """How to capture resources from an operation response.

    `pointer` is empty for the root; a payload that is the identifier itself goes under `identifier_field`.
    """

    resource_name: str
    operation: str
    status_code: str
    pointer: str
    cardinality: Cardinality
    is_primitive_identifier: bool = False
    identifier_field: str | None = None
    # The payload is a `{<id>: <object>, ...}` map whose keys are the identifiers.
    extract_object_keys: bool = False
