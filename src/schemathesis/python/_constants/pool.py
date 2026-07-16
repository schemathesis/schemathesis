from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal

ConstantType = Literal["string", "integer", "float", "bytes"]
ConstantValue = str | int | float | bytes

# Per-type cap on pool size; keeps memory bounded when a source harvests thousands of literals.
DEFAULT_CAP_PER_TYPE = 256


@dataclass(slots=True, frozen=True)
class Origin:
    """Where a constant value came from."""

    source: str
    """Registered source callable name (e.g. `from_my_app`)."""

    module: str
    """Fully-qualified module name (e.g. `my_app.config`)."""

    adapter: str | None
    """Framework adapter name when discovery flowed through one; `None` otherwise."""


@dataclass(slots=True, frozen=True)
class SourceFailure:
    """A registered constants source that produced nothing usable."""

    source: str
    reason: str


@dataclass(slots=True, frozen=True)
class ConstantEntry:
    """A literal value with its provenance."""

    value: ConstantValue
    type: ConstantType
    origins: tuple[Origin, ...]


@dataclass(slots=True, frozen=True)
class ConstantDraw:
    """Provenance for a single constant substituted into a generated case."""

    location: str
    parameter_name: str
    value: ConstantValue
    origin: Origin | None
    body_path: str | None = None


class ConstantsPool:
    """Typed pool of `ConstantEntry`s with origin merging and a per-type cap.

    Filled at engine init, read-only from the draw path's perspective.
    """

    __slots__ = ("_entries", "_cap", "_failures")

    def __init__(self, *, cap_per_type: int = DEFAULT_CAP_PER_TYPE) -> None:
        self._cap = cap_per_type
        self._entries: dict[ConstantType, OrderedDict[ConstantValue, ConstantEntry]] = {
            "string": OrderedDict(),
            "integer": OrderedDict(),
            "float": OrderedDict(),
            "bytes": OrderedDict(),
        }
        self._failures: list[SourceFailure] = []

    def record_failure(self, source: str, reason: str) -> None:
        self._failures.append(SourceFailure(source=source, reason=reason))

    @property
    def failures(self) -> tuple[SourceFailure, ...]:
        return tuple(self._failures)

    def add(self, entry: ConstantEntry) -> None:
        bucket = self._entries[entry.type]
        existing = bucket.get(entry.value)
        if existing is not None:
            merged = tuple(dict.fromkeys(existing.origins + entry.origins))
            bucket[entry.value] = ConstantEntry(value=existing.value, type=existing.type, origins=merged)
            return
        if len(bucket) >= self._cap:
            # Drop the oldest entry to keep memory bounded.
            bucket.popitem(last=False)
        bucket[entry.value] = entry

    def entries_for(self, type_: ConstantType) -> tuple[ConstantEntry, ...]:
        return tuple(self._entries[type_].values())

    def has_values_for(self, type_: ConstantType) -> bool:
        """Fast path: True if the pool has any values of `type_`, without materialising a tuple."""
        return bool(self._entries[type_])

    def is_empty(self) -> bool:
        return all(not bucket for bucket in self._entries.values())
