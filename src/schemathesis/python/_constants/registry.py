from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class SourceRegistration:
    """A registered source callable plus the name we attribute its output to."""

    name: str
    callable: Callable[[], object]


class SourceRegistry:
    """Holds registered source callables in registration order."""

    def __init__(self) -> None:
        self._entries: list[SourceRegistration] = []

    def entries(self) -> tuple[SourceRegistration, ...]:
        return tuple(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    @property
    def decorator(self) -> Callable[[Callable[[], object]], Callable[[], object]]:
        def register(fn: Callable[[], object]) -> Callable[[], object]:
            if not callable(fn):
                raise TypeError(f"@schemathesis.python.constants expects a callable, got {type(fn).__name__}")
            if any(e.callable is fn for e in self._entries):
                return fn
            self._entries.append(SourceRegistration(name=fn.__name__, callable=fn))
            return fn

        return register


_default_registry = SourceRegistry()
constants = _default_registry.decorator
"""User-facing decorator: `@schemathesis.python.constants`."""


def default_registry() -> SourceRegistry:
    return _default_registry
