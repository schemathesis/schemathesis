from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum

from schemathesis.core.parameters import ParameterLocation

# Bound per (operation, location) bucket; lowest-count entry is evicted at the cap.
MAX_ENTRIES_PER_BUCKET = 100
# Below this, an observation is treated as a fluke and not surfaced to adjustments.
MIN_OBSERVATIONS = 2


class ObservationKind(str, Enum):
    """Kinds of signal a parser can extract from a 4xx body."""

    MUST_NOT_BE_BLANK = "must_not_be_blank"
    SIZE_BOUND = "size_bound"


@dataclass(frozen=True, slots=True)
class SizeBoundPayload:
    """Numeric size bounds extracted from a Bean-validation `@Size`/`@Length` message.

    Applies to whatever JSON-Schema container the field resolves to: strings
    (`minLength`/`maxLength`), arrays (`minItems`/`maxItems`), or objects
    (`minProperties`/`maxProperties`).
    """

    min: int
    max: int


ObservationPayload = SizeBoundPayload | None


@dataclass(frozen=True, slots=True)
class Observation:
    """One field-level signal observed from a single 4xx response."""

    operation_label: str
    location: ParameterLocation
    parameter_path: tuple[str | int, ...]
    kind: ObservationKind
    raw_message: str
    payload: ObservationPayload = None


_EntryKey = tuple[tuple[str | int, ...], ObservationKind]
_BucketKey = tuple[str, ParameterLocation]


@dataclass(slots=True)
class _Entry:
    """Dedup state for one (path, kind): canonical first-seen Observation + running count."""

    canonical: Observation
    count: int = 0
    last_message: str = ""


@dataclass(slots=True)
class _Bucket:
    entries: dict[_EntryKey, _Entry] = field(default_factory=dict)


class ErrorFeedbackStore:
    """Thread-safe accumulator of parser observations + monotonic generation counter.

    `generation` advances at engine `checkpoint()` calls; strategy caches key on
    it and rebuild only when it bumps. Within one suite the value is fixed —
    safe to consult from inside Hypothesis-driven generation.
    """

    __slots__ = ("_buckets", "_lock", "_generation")

    def __init__(self) -> None:
        self._buckets: dict[_BucketKey, _Bucket] = {}
        self._lock = threading.Lock()
        self._generation = 0

    @property
    def generation(self) -> int:
        return self._generation

    def checkpoint(self) -> None:
        """Engine-side boundary marker; never call from inside a Hypothesis suite."""
        with self._lock:
            self._generation += 1

    def record(self, observation: Observation) -> None:
        """Insert with dedup on (path, kind); evicts the lowest-count entry when full."""
        bucket_key: _BucketKey = (observation.operation_label, observation.location)
        entry_key: _EntryKey = (observation.parameter_path, observation.kind)
        with self._lock:
            bucket = self._buckets.setdefault(bucket_key, _Bucket())
            entry = bucket.entries.get(entry_key)
            if entry is None:
                if len(bucket.entries) >= MAX_ENTRIES_PER_BUCKET:
                    # Drop the weakest signal so a noisy bucket can't lock out new ones.
                    victim = min(bucket.entries, key=lambda k: bucket.entries[k].count)
                    del bucket.entries[victim]
                bucket.entries[entry_key] = _Entry(
                    canonical=observation,
                    count=1,
                    last_message=observation.raw_message,
                )
            else:
                entry.count += 1
                entry.last_message = observation.raw_message

    def observations(
        self,
        *,
        operation_label: str,
        location: ParameterLocation,
        min_count: int = MIN_OBSERVATIONS,
    ) -> tuple[Observation, ...]:
        """Immutable snapshot for one (op, location), filtered by calibration threshold."""
        with self._lock:
            bucket = self._buckets.get((operation_label, location))
            if bucket is None:
                return ()
            return tuple(entry.canonical for entry in bucket.entries.values() if entry.count >= min_count)
