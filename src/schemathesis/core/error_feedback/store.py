from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TypeAlias

from schemathesis.core.parameters import ParameterLocation

# Path into a request payload: strings index objects, integers index arrays.
ParameterPath: TypeAlias = tuple[str | int, ...]

# Bound per (operation, location) bucket; lowest-count entry is evicted at the cap.
MAX_ENTRIES_PER_BUCKET = 100


class ObservationKind(StrEnum):
    """Kinds of signal a parser can extract from a 4xx body."""

    MUST_NOT_BE_BLANK = "must_not_be_blank"
    SIZE_BOUND = "size_bound"
    FORMAT = "format"
    NUMERIC_BOUND = "numeric_bound"
    PATTERN = "pattern"
    TYPE_MISMATCH = "type_mismatch"
    ENUM = "enum"
    REQUIRES_AUTHENTICATION = "requires_authentication"
    UNEXPECTED_PROPERTY = "unexpected_property"
    FORBIDS_ADDITIONAL_PROPERTIES = "forbids_additional_properties"


@dataclass(frozen=True, slots=True)
class SizeBoundPayload:
    """Size bounds of a string, array or object (`minLength`, `minItems`, `minProperties` and their max pairs).

    A bound is `None` when the error names only the violated side; the store merges complementary observations.
    """

    min: int | None
    max: int | None


@dataclass(frozen=True, slots=True)
class FormatPayload:
    """JSON Schema `format` name (`email`, `uri`, `uuid`, ...), applied only to properties that declare no format."""

    name: str


class BoundDirection(StrEnum):
    MIN = "min"
    MAX = "max"


@dataclass(frozen=True, slots=True)
class NumericBoundPayload:
    """One side of a numeric range; `exclusive` excludes the bound itself."""

    bound: int | float
    direction: BoundDirection
    exclusive: bool


@dataclass(frozen=True, slots=True)
class PatternPayload:
    """Regex taken verbatim from an error message.

    The consumer rewrites Java-only constructs (PCRE escapes, POSIX classes, Python anchors) before writing `pattern`.
    """

    regex: str


@dataclass(frozen=True, slots=True)
class TypeMismatchPayload:
    """A JSON Schema type name, or a framework type such as a Java class name.

    The consumer fixes `type` for JSON Schema type names and maps anything else to a `format`.
    """

    type_name: str


@dataclass(frozen=True, slots=True)
class EnumPayload:
    """Allowed values from an error message, written as the property's `enum`."""

    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RequiresAuthPayload:
    """The operation requires the named security scheme; consumers add it to the operation's security requirements.

    Recorded when a retry with the scheme's configured credentials succeeds after an auth failure.
    """

    scheme_name: str


ObservationPayload = (
    SizeBoundPayload
    | FormatPayload
    | NumericBoundPayload
    | PatternPayload
    | TypeMismatchPayload
    | EnumPayload
    | RequiresAuthPayload
    | None
)


@dataclass(frozen=True, slots=True)
class Observation:
    """One field-level signal observed from a single 4xx response."""

    operation_label: str
    location: ParameterLocation
    parameter_path: ParameterPath
    kind: ObservationKind
    raw_message: str
    payload: ObservationPayload = None


_EntryKey = tuple[ParameterPath, ObservationKind, object]
_BucketKey = tuple[str, ParameterLocation]


def _entry_key(observation: Observation) -> _EntryKey:
    # Each payload variant on one (path, kind) gets its own slot, except size bounds, whose min and max merge.
    payload = observation.payload
    discriminator: BoundDirection | str | tuple[str, ...] | None
    if isinstance(payload, NumericBoundPayload):
        discriminator = payload.direction
    elif isinstance(payload, FormatPayload):
        discriminator = payload.name
    elif isinstance(payload, PatternPayload):
        discriminator = payload.regex
    elif isinstance(payload, EnumPayload):
        discriminator = payload.values
    elif isinstance(payload, TypeMismatchPayload):
        discriminator = payload.type_name
    else:
        discriminator = None
    return (observation.parameter_path, observation.kind, discriminator)


def observation_fingerprint(observation: Observation) -> str:
    """Dedup key across all buckets: `(operation_label, location, parameter_path, kind, payload-discriminator)`."""
    bucket = observation.operation_label, observation.location.value
    path, kind, discriminator = _entry_key(observation)
    if isinstance(discriminator, BoundDirection):
        discriminator = discriminator.value
    return repr((bucket, tuple(path), kind.value, discriminator))


@dataclass(slots=True)
class _Entry:
    """Dedup state for one (path, kind): canonical first-seen Observation + running count."""

    canonical: Observation
    count: int = 0


@dataclass(slots=True)
class _Bucket:
    entries: dict[_EntryKey, _Entry] = field(default_factory=dict)
    # Distinct signals ever inserted here; unlike `entries` it survives eviction at the cap.
    inserted: int = 0


class ErrorFeedbackStore:
    """Thread-safe store of parser observations with a `generation` counter that strategy caches key on.

    `generation` changes only at `checkpoint()` after new feedback, so it stays fixed during a Hypothesis suite.
    """

    __slots__ = ("_buckets", "_lock", "_generation", "_changed")

    def __init__(self) -> None:
        self._buckets: dict[_BucketKey, _Bucket] = {}
        self._lock = threading.Lock()
        self._generation = 0
        # Whether the observations visible to strategies changed since the last checkpoint.
        self._changed = False

    @property
    def generation(self) -> int:
        return self._generation

    def checkpoint(self) -> None:
        """Engine-side boundary marker; never call from inside a Hypothesis suite."""
        with self._lock:
            if self._changed:
                self._generation += 1
                self._changed = False

    def observation_counts(self) -> dict[str, int]:
        """Distinct observations ever recorded per operation, evicted ones included."""
        counts: dict[str, int] = {}
        with self._lock:
            for (label, _), bucket in self._buckets.items():
                counts[label] = counts.get(label, 0) + bucket.inserted
        return counts

    def distinct_observations(self) -> int:
        with self._lock:
            return sum(len(bucket.entries) for bucket in self._buckets.values())

    def record(self, observation: Observation) -> None:
        """Insert with dedup on (path, kind, payload-discriminator); evicts the lowest-count entry when full."""
        bucket_key: _BucketKey = (observation.operation_label, observation.location)
        entry_key = _entry_key(observation)
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
                )
                bucket.inserted += 1
                self._changed = True
            else:
                entry.count += 1
                # Validators may report one side of a size bound; merge both sides into the canonical payload.
                if isinstance(observation.payload, SizeBoundPayload) and isinstance(
                    entry.canonical.payload, SizeBoundPayload
                ):
                    merged = SizeBoundPayload(
                        min=observation.payload.min
                        if observation.payload.min is not None
                        else entry.canonical.payload.min,
                        max=observation.payload.max
                        if observation.payload.max is not None
                        else entry.canonical.payload.max,
                    )
                    if merged != entry.canonical.payload:
                        entry.canonical = replace(entry.canonical, payload=merged)
                        self._changed = True

    def observations(
        self,
        *,
        operation_label: str,
        location: ParameterLocation,
    ) -> tuple[Observation, ...]:
        """Snapshot of every observation for one (operation, location).

        One occurrence suffices: parsers emit only on framework-specific matches; waiting lets wrong examples through.
        """
        with self._lock:
            bucket = self._buckets.get((operation_label, location))
            if bucket is None:
                return ()
            return tuple(entry.canonical for entry in bucket.entries.values())
