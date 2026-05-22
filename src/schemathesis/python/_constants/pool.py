from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from random import Random
from typing import Literal

ConstantType = Literal["string", "integer", "float", "bytes"]
ConstantValue = str | int | float | bytes


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
class ConstantEntry:
    """A literal value with its provenance."""

    value: ConstantValue
    type: ConstantType
    origins: tuple[Origin, ...]


class ConstantsPool:
    """Typed pool of `ConstantEntry`s with origin merging and a per-type cap.

    Filled at engine init, read-only from the draw path's perspective.
    """

    __slots__ = ("_entries", "_cap")

    def __init__(self, *, cap_per_type: int = 5000) -> None:
        self._cap = cap_per_type
        self._entries: dict[ConstantType, OrderedDict[ConstantValue, ConstantEntry]] = {
            "string": OrderedDict(),
            "integer": OrderedDict(),
            "float": OrderedDict(),
            "bytes": OrderedDict(),
        }

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

    def values_for(self, type_: ConstantType) -> tuple[ConstantValue, ...]:
        return tuple(self._entries[type_].keys())

    def has_values_for(self, type_: ConstantType) -> bool:
        """Fast path: True if the pool has any values of `type_`, without materialising a tuple."""
        return bool(self._entries[type_])

    def is_empty(self) -> bool:
        return all(not bucket for bucket in self._entries.values())

    def count(self, type_: ConstantType) -> int:
        return len(self._entries[type_])


class ConstantsValueSource:
    """Engine-facing handle that exposes draws from a `ConstantsPool`.

    Wraps a pool so the strategy stack can ask `is_active(type)` cheaply and pull a
    uniform-random value at draw time. The probability of consulting this source
    is controlled at the overlay-composition layer (see `parameters.py`).
    """

    __slots__ = ("_pool",)

    def __init__(self, pool: ConstantsPool) -> None:
        self._pool = pool

    def is_active(self, type_: ConstantType) -> bool:
        return self._pool.has_values_for(type_)

    def draw(self, type_: ConstantType, *, rng: Random) -> ConstantValue | None:
        values = self._pool.values_for(type_)
        if not values:
            return None
        return rng.choice(values)

    @property
    def pool(self) -> ConstantsPool:
        return self._pool
