from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from schemathesis.core.parameters import ParameterLocation, SkippedParameter

if TYPE_CHECKING:
    from schemathesis.core.jsonschema.types import JsonSchemaObject

T = TypeVar("T", covariant=True)


class ResponsesContainer(Protocol[T]):
    def find_by_status_code(self, status_code: int) -> T | None: ...  # pragma: no cover
    def add(self, status_code: str, definition: dict[str, Any]) -> T: ...  # pragma: no cover
    @property
    def status_codes(self) -> tuple[str, ...]: ...  # pragma: no cover


class OperationParameter(Protocol):
    @property
    def definition(self) -> JsonSchemaObject: ...  # pragma: no cover

    @property
    def media_type(self) -> str | None:
        """Media type for body parameters; `None` for parameters at other locations."""
        ...  # pragma: no cover

    @property
    def location(self) -> ParameterLocation: ...  # pragma: no cover

    @property
    def name(self) -> str: ...  # pragma: no cover

    @property
    def is_required(self) -> bool: ...  # pragma: no cover

    def validate(self, value: Any) -> None:
        """Raise if `value` does not conform to this parameter's schema."""
        ...  # pragma: no cover

    def is_valid(self, value: Any) -> bool: ...  # pragma: no cover


@dataclass(slots=True)
class ParsedParameters:
    """Parameters of one API operation, plus those skipped as unparsable."""

    items: list[OperationParameter]
    skipped: list[SkippedParameter]
