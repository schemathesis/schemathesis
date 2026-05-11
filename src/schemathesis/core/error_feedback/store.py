from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from enum import Enum

from schemathesis.core.parameters import ParameterLocation

# Bound per (operation, location) bucket; lowest-count entry is evicted at the cap.
MAX_ENTRIES_PER_BUCKET = 100


class ObservationKind(str, Enum):
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


@dataclass(frozen=True, slots=True)
class SizeBoundPayload:
    """Numeric size bounds extracted from a size-constraint validation error.

    Either bound may be `None` when the source error reports only the violated
    side; complementary observations are merged into a single canonical entry
    by the store. Applies to whatever JSON-Schema container the field resolves
    to: strings (`minLength`/`maxLength`), arrays (`minItems`/`maxItems`), or
    objects (`minProperties`/`maxProperties`).
    """

    min: int | None
    max: int | None


@dataclass(frozen=True, slots=True)
class FormatPayload:
    """JSON-Schema `format` name inferred from a Bean-validation message.

    The string is a JSON-Schema format name (`email`, `uri`, `uuid`, ...). The
    consumer adjustment writes it as `format: <name>` on the resolved property,
    only when no format is already declared.
    """

    name: str


class BoundDirection(str, Enum):
    """Which side of a numeric range a `NumericBoundPayload` constrains."""

    MIN = "min"
    MAX = "max"


@dataclass(frozen=True, slots=True)
class NumericBoundPayload:
    """One half of a numeric range — `direction` picks which side, `exclusive` whether the bound itself is excluded."""

    bound: float
    direction: BoundDirection
    exclusive: bool


@dataclass(frozen=True, slots=True)
class PatternPayload:
    """Regex captured verbatim from a Bean-validation `@Pattern` message.

    The consumer normalizes Java-only constructs (PCRE escapes, POSIX classes,
    Python anchors) before writing it as `pattern: <regex>` onto the property.
    """

    regex: str


@dataclass(frozen=True, slots=True)
class TypeMismatchPayload:
    """Framework-specific type identifier — Java FQN from Jackson/Spring, JSON-Schema type token from DRF.

    The consumer dispatches by inspecting the value: closed-set JSON-Schema types correct schema
    `type`; anything else is treated as a Java FQN and mapped to JSON-Schema `format`.
    """

    type_name: str


@dataclass(frozen=True, slots=True)
class EnumPayload:
    """Enum value list extracted from a Jackson `not one of the values accepted for Enum class: [...]` message.

    The consumer writes it as `enum: [...]` onto the property.
    """

    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RequiresAuthPayload:
    """Operation-level signal: the named scheme is required to reach the operation.

    Recorded after an auth-failure response is followed by a confirmation retry that
    attached the scheme's configured credentials and succeeded. Consumers attach the
    scheme to the operation's effective security requirements.
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
    parameter_path: tuple[str | int, ...]
    kind: ObservationKind
    raw_message: str
    payload: ObservationPayload = None


_EntryKey = tuple[tuple[str | int, ...], ObservationKind, object]
_BucketKey = tuple[str, ParameterLocation]


def _entry_key(observation: Observation) -> _EntryKey:
    # MIN and MAX numeric bounds for the same path are independent constraints —
    # discriminate them so both sides survive dedup.
    if isinstance(observation.payload, NumericBoundPayload):
        return (observation.parameter_path, observation.kind, observation.payload.direction)
    return (observation.parameter_path, observation.kind, None)


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
                    last_message=observation.raw_message,
                )
            else:
                entry.count += 1
                entry.last_message = observation.raw_message
                # Some validators report only the violated side of a size bound;
                # fold complementary observations together so the canonical payload
                # carries both edges once they have both been seen.
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

    def observations(
        self,
        *,
        operation_label: str,
        location: ParameterLocation,
    ) -> tuple[Observation, ...]:
        """Immutable snapshot of every observation recorded for one (op, location).

        Parsers only emit observations when their framework-specific regex matches
        an error envelope, so a single occurrence is conclusive enough to act on —
        delaying propagation until a second occurrence pushes the wrong spec
        example through the next phase before the constraint can land.
        """
        with self._lock:
            bucket = self._buckets.get((operation_label, location))
            if bucket is None:
                return ()
            return tuple(entry.canonical for entry in bucket.entries.values())
