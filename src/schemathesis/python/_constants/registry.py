from __future__ import annotations

from collections.abc import Callable

Source = Callable[[], object]


class SourceRegistry:
    """Ordered, identity-keyed set of constant sources.

    Keyed by function identity rather than name so two sources that happen to share a
    ``__name__`` (a common case across modules) both survive instead of overwriting.
    """

    __slots__ = ("_sources", "_version")

    def __init__(self) -> None:
        self._sources: list[Source] = []
        # Bumped on every mutation so extraction can memoise a pool and invalidate it on change.
        self._version = 0

    def register(self, source: Source) -> Source:
        if not any(existing is source for existing in self._sources):
            self._sources.append(source)
            self._version += 1
        return source

    def get_all(self) -> list[Source]:
        return list(self._sources)

    def clear(self) -> None:
        if self._sources:
            self._sources.clear()
            self._version += 1

    @property
    def version(self) -> int:
        return self._version


_default_registry = SourceRegistry()


def constants(fn: Callable[[], object]) -> Callable[[], object]:
    """Register a source of constants for test generation.

    Decorate a zero-argument function that returns your app, module(s), or objects.
    Schemathesis harvests the literal values defined in that code and reuses them when
    generating test data.
    """
    if not callable(fn):
        raise TypeError(f"@schemathesis.python.constants expects a callable, got {type(fn).__name__}")
    return _default_registry.register(fn)


def default_registry() -> SourceRegistry:
    return _default_registry
