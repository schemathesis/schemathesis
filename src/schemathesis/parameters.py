"""API operation parameters.

These are basic entities, that describe what data could be sent to the API.
"""
from copy import deepcopy
from typing import Any, Dict, Generator, Generic, List, TypeVar

import attr


@attr.s(slots=True)
class Parameter:
    """A logically separate parameter bound to a location (e.g. to "query string").

    For example, if the API requires multiple headers to be present, then each header is presented as a separate
    `Parameter` instance.
    """

    # The parameter definition in the language acceptable by the API
    definition: Any = attr.ib()

    def __attrs_post_init__(self) -> None:
        # Do not use `converter=deepcopy` on the field, due to mypy not detecting type annotations
        self.definition = deepcopy(self.definition)

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

    @property
    def example(self) -> Any:
        """Parameter example."""
        return None


P = TypeVar("P", bound=Parameter)


@attr.s(slots=True)
class ParameterSet(Generic[P]):
    """A set of parameters for the same location."""

    items: List[P] = attr.ib(factory=list)

    def add(self, parameter: P) -> None:
        self.items.append(parameter)

    @property
    def example(self) -> Dict[str, Any]:
        return {item.name: item.example for item in self.items if item.example}

    def __bool__(self) -> bool:
        return bool(self.items)

    def __iter__(self) -> Generator[P, None, None]:
        yield from iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, item: int) -> P:
        return self.items[item]


@attr.s(slots=True)
class PayloadAlternatives(ParameterSet[P]):
    """A set of alternative payloads."""

    @property
    def example(self) -> Any:
        """We take only the first example."""
        # May be extended in the future
        if self.items:
            return self.items[0].example
