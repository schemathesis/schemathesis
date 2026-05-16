from __future__ import annotations

from collections.abc import Callable
from operator import attrgetter
from typing import Any, ClassVar, Generic, TypeVar, overload

T = TypeVar("T")

_UNSET: Any = object()


class LazyInit(Generic[T]):
    """Descriptor that lazily computes a per-instance value under a per-instance lock.

    Host class declares slots `_{name}` (init to `LazyInit.UNSET`) and `_{name}_lock`. `None` is a
    valid cached value, distinct from `UNSET`.
    """

    UNSET: ClassVar[Any] = _UNSET

    __slots__ = ("_factory", "_value_attr", "_get_value", "_get_lock")

    def __init__(self, factory: Callable[[Any], T]) -> None:
        self._factory = factory

    def __set_name__(self, owner: type, name: str) -> None:
        # C-level `attrgetter` avoids the per-read overhead of name-keyed `getattr` on the cached fast path.
        self._value_attr = f"_{name}"
        self._get_value = attrgetter(self._value_attr)
        self._get_lock = attrgetter(f"_{name}_lock")

    @overload
    def __get__(self, instance: None, owner: type) -> LazyInit[T]: ...
    @overload
    def __get__(self, instance: object, owner: type) -> T: ...
    def __get__(self, instance: object | None, owner: type | None = None) -> T | LazyInit[T]:
        if instance is None:
            return self
        value = self._get_value(instance)
        if value is _UNSET:
            with self._get_lock(instance):
                value = self._get_value(instance)
                if value is _UNSET:
                    value = self._factory(instance)
                    setattr(instance, self._value_attr, value)
        return value
