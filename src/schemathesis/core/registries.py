from __future__ import annotations

from typing import Callable, Generic, Sequence, TypeVar, Union

T = TypeVar("T", bound=Union[Callable, type])


class Registry(Generic[T]):
    """Container for Schemathesis extensions."""

    __slots__ = ("_items",)

    def __init__(self) -> None:
        self._items: dict[str, T] = {}

    def register(self, item: T) -> T:
        self._items[item.__name__] = item
        return item

    def unregister(self, name: str) -> None:
        del self._items[name]

    def get_all_names(self) -> list[str]:
        return list(self._items)

    def get_all(self) -> list[T]:
        return list(self._items.values())

    def get_by_names(self, names: Sequence[str]) -> list[T]:
        """Get items by their names."""
        return [self._items[name] for name in names]
