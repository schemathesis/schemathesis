from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Iterator, Protocol, TypeVar

from schemathesis.core.errors import InvalidSchema
from schemathesis.core.result import Result

T = TypeVar("T")


class ApiOperationLoader(Protocol):
    def __init__(self, specification: ApiSpecification) -> None: ...

    def iter_operations(self) -> Iterator[Result[ApiOperation, InvalidSchema]]: ...


@dataclass
class ApiSpecification:
    """A way to access components of an API specification."""

    name: str
    # Raw specification data
    data: dict[str, Any]
    loader: ApiOperationLoader

    __slots__ = ("name", "data", "loader")

    def __init__(self, name: str, data: dict[str, Any], loader: type[ApiOperationLoader]) -> None:
        self.name = name
        self.data = data
        self.loader = loader(self)

    def iter_operations(self) -> Iterator[Result[ApiOperation[object], InvalidSchema]]:
        yield from self.loader.iter_operations()


@dataclass
class ApiOperation(Generic[T]):
    """An action provided by an API."""

    specification: ApiSpecification
    label: str
    data: T

    __slots__ = ("specification", "label", "data")
