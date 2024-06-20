"""API operation parameters.

These are basic entities that describe what data could be sent to the API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generator, Generic, TypeVar

if TYPE_CHECKING:
    from .models import APIOperation


@dataclass(eq=False)
class Parameter:
    """A logically separate parameter bound to a location (e.g., to "query string").

    For example, if the API requires multiple headers to be present, each header is presented as a separate
    `Parameter` instance.
    """

    # The parameter definition in the language acceptable by the API
    definition: Any

    @property
    def location(self) -> str:
        """Where this parameter is located.

        E.g. "query" or "body"
        """
        raise NotImplementedError

    @property
    def name(self) -> str:
        """Parameter name."""
        raise NotImplementedError

    @property
    def is_required(self) -> bool:
        """Whether the parameter is required for a successful API call."""
        raise NotImplementedError

    def serialize(self, operation: APIOperation) -> str:
        """Get parameter's string representation."""
        raise NotImplementedError


P = TypeVar("P", bound=Parameter)


@dataclass
class ParameterSet(Generic[P]):
    """A set of parameters for the same location."""

    items: list[P] = field(default_factory=list)

    def add(self, parameter: P) -> None:
        """Add a new parameter."""
        self.items.append(parameter)

    def get(self, name: str) -> P | None:
        for parameter in self:
            if parameter.name == name:
                return parameter
        return None

    def contains(self, name: str) -> bool:
        return self.get(name) is not None

    def __contains__(self, item: str) -> bool:
        return self.contains(item)

    def __bool__(self) -> bool:
        return bool(self.items)

    def __iter__(self) -> Generator[P, None, None]:
        yield from iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, item: int) -> P:
        return self.items[item]


class PayloadAlternatives(ParameterSet[P]):
    """A set of alternative payloads."""
