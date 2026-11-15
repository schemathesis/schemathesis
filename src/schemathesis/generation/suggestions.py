"""Values that hooks suggest for request parameters while tests run."""

from __future__ import annotations

import threading
from collections.abc import Iterable

from schemathesis.config._dictionaries import (
    DictionaryBinding,
    DictionaryDefinition,
    DictionaryEntry,
    EntryValue,
    ParameterDictionaryBinding,
)

# Higher values crowd out generated data and the code paths only it reaches.
DEFAULT_PROBABILITY = 0.3
# Building a strategy costs time for every value it can draw; the oldest values go first.
MAX_VALUES = 1000
SOURCE_KIND = "suggestion"
# Suggestions made outside of any extension.
HOOKS_SOURCE = "hooks"


class Suggestions:
    """Suggested values of one schema, shaped as dictionaries and bindings so they are drawn the same way."""

    __slots__ = ("dictionaries", "_generations", "_parameters", "_types", "_lock")

    def __init__(self) -> None:
        self.dictionaries: dict[str, DictionaryDefinition] = {}
        # New values per operation label; `None` counts the ones for every operation.
        self._generations: dict[str | None, int] = {}
        # Bindings by operation label; `None` holds the ones for every operation.
        self._parameters: dict[str | None, dict[str, ParameterDictionaryBinding]] = {}
        self._types: dict[str | None, dict[str, DictionaryBinding]] = {}
        self._lock = threading.Lock()

    def add(
        self,
        values: Iterable[EntryValue],
        *,
        source: str,
        operation: str | None,
        parameter: str | None,
        probability: float,
    ) -> None:
        with self._lock:
            if parameter is not None:
                name = f"{source}:{operation}:{parameter}"
                self._parameters.setdefault(operation, {})[parameter] = ParameterDictionaryBinding(name, probability)
                self._extend(name, source, operation, values)
                return
            for value in values:
                for ty in _types_of(value):
                    name = f"{source}:{operation}:{ty}"
                    self._types.setdefault(operation, {})[ty] = DictionaryBinding(name, probability)
                    self._extend(name, source, operation, [value])

    def _extend(self, name: str, source: str, operation: str | None, values: Iterable[EntryValue]) -> None:
        definition = self.dictionaries.get(name)
        if definition is None:
            definition = self.dictionaries[name] = DictionaryDefinition(
                name=source, entries=(), source_kind=SOURCE_KIND, source_path=None
            )
        known = {entry.value for entry in definition.entries}
        new = [value for value in dict.fromkeys(values) if value not in known]
        if new:
            start = definition.entries[-1].index + 1 if definition.entries else 0
            entries = definition.entries + tuple(
                DictionaryEntry(value, start + index) for index, value in enumerate(new)
            )
            definition.entries = entries[-MAX_VALUES:]
            self._generations[operation] = self._generations.get(operation, 0) + 1

    def generation(self, operation: str) -> int:
        """Changes with every new value the operation can draw, so its strategies are rebuilt."""
        return self._generations.get(None, 0) + self._generations.get(operation, 0)

    def parameters(self, operation: str) -> tuple[dict[str, ParameterDictionaryBinding], ...]:
        """Parameter bindings for the operation, the most specific first."""
        return tuple(self._parameters[scope] for scope in (operation, None) if scope in self._parameters)

    def types(self, operation: str) -> dict[str, DictionaryBinding]:
        """Type-wide bindings for the operation."""
        return {**self._types.get(None, {}), **self._types.get(operation, {})}


def _types_of(value: EntryValue) -> tuple[str, ...]:
    if isinstance(value, str):
        return ("string",)
    if isinstance(value, int):
        return ("integer", "number")
    return ("number",)
