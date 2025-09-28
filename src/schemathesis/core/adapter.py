from __future__ import annotations

from typing import Any, Protocol, TypeVar

from schemathesis.core.parameters import ParameterLocation

T = TypeVar("T", covariant=True)


class ResponsesContainer(Protocol[T]):
    def find_by_status_code(self, status_code: int) -> T | None: ...  # pragma: no cover
    def add(self, status_code: str, definition: dict[str, Any]) -> T: ...  # pragma: no cover


class OperationParameter(Protocol):
    """API parameter at a specific location (query, header, body, etc.)."""

    definition: Any
    """Raw parameter definition from the API spec."""

    @property
    def location(self) -> ParameterLocation:
        """Location: "query", "header", "body", etc."""
        ...  # pragma: no cover

    @property
    def name(self) -> str:
        """Parameter name."""
        ...  # pragma: no cover

    @property
    def is_required(self) -> bool:
        """True if required."""
        ...  # pragma: no cover
