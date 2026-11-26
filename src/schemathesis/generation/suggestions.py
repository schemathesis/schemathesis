"""Values that hooks suggest for request parameters while tests run."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import TYPE_CHECKING

import jsonschema_rs

from schemathesis.config._dictionaries import (
    DictionaryDefinition,
    DictionaryEntry,
    EntrySchema,
    EntryValue,
    ParameterDictionaryBinding,
)

if TYPE_CHECKING:
    from schemathesis.core.jsonschema.types import JsonSchemaObject

# Higher values crowd out generated data and the code paths only it reaches.
DEFAULT_PROBABILITY = 0.3
# Building a strategy costs time for every value it can draw; the oldest values go first.
MAX_VALUES = 1000
SOURCE_KIND = "suggestion"
# Suggestions made outside of any extension.
HOOKS_SOURCE = "hooks"


class Suggestions:
    """Suggested values of one schema, shaped as dictionaries and bindings so they are drawn the same way."""

    __slots__ = ("dictionaries", "_generations", "_parameters", "_lock")

    def __init__(self) -> None:
        self.dictionaries: dict[str, DictionaryDefinition] = {}
        # New values per operation label; `None` counts the ones for every operation.
        self._generations: dict[str | None, int] = {}
        # Bindings by operation label; `None` holds the ones for every operation.
        self._parameters: dict[str | None, dict[str, ParameterDictionaryBinding]] = {}
        self._lock = threading.Lock()

    def add(
        self,
        schemas: Iterable[JsonSchemaObject],
        *,
        source: str,
        operation: str | None,
        parameter: str,
        probability: float = DEFAULT_PROBABILITY,
    ) -> None:
        entries: list[EntryValue | EntrySchema] = []
        for schema in schemas:
            fixed = _fixed_values(schema)
            if fixed is not None:
                entries.extend(fixed)
            else:
                entries.append(EntrySchema(jsonschema_rs.canonical.json.to_string(schema)))
        name = f"{source}:{operation}:{parameter}"
        with self._lock:
            self._parameters.setdefault(operation, {})[parameter] = ParameterDictionaryBinding(name, probability)
            self._extend(name, source, operation, entries)

    def _extend(
        self, name: str, source: str, operation: str | None, values: Iterable[EntryValue | EntrySchema]
    ) -> None:
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


def _fixed_values(schema: JsonSchemaObject) -> list[EntryValue] | None:
    """The texts and numbers a schema lists one by one; `None` when it describes its values another way."""
    try:
        view = jsonschema_rs.canonicalize(schema).view()
    except jsonschema_rs.canonical.CanonicalizationError:
        return None
    if isinstance(view, jsonschema_rs.canonical.ConstView):
        members = [view.value]
    elif isinstance(view, jsonschema_rs.canonical.EnumView):
        members = view.values
    else:
        return None
    return [member for member in members if isinstance(member, (str, int, float)) and not isinstance(member, bool)]
