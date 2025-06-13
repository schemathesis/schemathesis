"""Shared interface to work with API specifications."""

from typing import Any, Iterator, Protocol

from schemathesis.core.errors import InvalidSchema
from schemathesis.core.result import Result

ApiOperationResult = Result[Any, InvalidSchema]


class Specification(Protocol):
    def __iter__(self) -> Iterator[ApiOperationResult]:
        """Iterating over API operations."""
        ...
