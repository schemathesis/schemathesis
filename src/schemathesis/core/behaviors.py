"""Counting how many different things an API operation did during a run.

Two responses are the same behavior when they say the same thing: the status code, the values in
the body, and the headers. `{"status": "archived"}` differs from `{"status": "active"}`.

Fields whose values rarely repeat hold ids, not answers, and are left out - a thousand responses
with a thousand ids are one behavior.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

__all__ = ["BehaviorAlphabet", "BehaviorCensus", "BehaviorSummary", "value_label"]

# Longer values are prose or payloads, not answers. 48 doubled how well behaviors were told
# apart on recorded runs; higher gained nothing.
MAX_LABEL_LENGTH = 48
# How often a field's values must repeat to count. Below it, the field holds ids. Real runs show
# no natural cutoff and few fields land near this one, so its exact value matters little.
MIN_SAMPLE_COVERAGE = 0.5
# Fields seen fewer times than this are kept regardless: too early to tell an id from a rare
# behavior, and losing a rare behavior is the worse mistake.
MIN_OBSERVATIONS_TO_JUDGE = 20
# Memory caps. They change nothing else.
MAX_TRACKED_VALUES = 1_000
MAX_RETAINED_KEYS = 10_000
# Keeps a `date` header apart from a `date` body field.
HEADER_PREFIX = "h:"
# Stand-in field names for bodies that are not JSON objects. They also separate body types,
# since an array and an object never share field names. The exception: `{}` and no body both say
# nothing, so they count as one behavior.
TEXT = "<text>"
ELEMENTS = "<elements>"
VALUE = "<value>"
# Enough of a text body to tell two error pages apart, not so much that a stack trace makes
# every response unique.
TEXT_PREFIX_LENGTH = 120

DIGITS = re.compile(r"\d+")
WHITESPACE = re.compile(r"\s+")

Labels = tuple[tuple[str, str], ...]


def _as_label(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value)
    return text[:MAX_LABEL_LENGTH]


def value_label(value: object) -> str | None:
    """What one value says, or `None` if it says nothing.

    Numbers, booleans and nulls count: `{"success": false}` is an answer. A numeric id is still
    ignored, but for never repeating rather than for being a number.
    """
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, str):
        text = value
    elif isinstance(value, (int, float)):
        text = repr(value)
    elif value is None:
        text = "null"
    else:
        return None
    return text if len(text) <= MAX_LABEL_LENGTH else None


def _normalize_text(body: bytes) -> str:
    """The start of a text body, with numbers and spacing removed.

    Two copies of one error page differ only in those, so both end up saying the same thing.
    """
    text = body.decode("utf-8", "replace")[:TEXT_PREFIX_LENGTH]
    return DIGITS.sub("N", WHITESPACE.sub(" ", text)).strip()


def _object_labels(payload: dict, prefix: str = "") -> Labels:
    """What an object says, including one level of nesting.

    Without it, `{"data": {"state": "active"}}` would say nothing - the answer is inside the
    wrapper.
    """
    labels = []
    for name, value in sorted(payload.items()):
        full = f"{prefix}.{name}" if prefix else name
        label = value_label(value)
        if label is not None:
            labels.append((full, label))
        elif isinstance(value, dict) and not prefix:
            labels.extend(_object_labels(value, full))
    return tuple(labels)


def _summarise_elements(payload: list) -> str:
    """What an array says: the different short strings in it, or the kinds of thing it holds."""
    names = sorted({item for item in payload if isinstance(item, str) and len(item) <= MAX_LABEL_LENGTH})
    if names:
        return ",".join(names)[:MAX_LABEL_LENGTH]
    return ",".join(sorted({type(item).__name__ for item in payload}))


SpeciesKey = tuple[int, Labels]


@dataclass(slots=True)
class _Seen:
    """How often one behavior occurred, and which response first showed it."""

    count: int
    first: int


@dataclass
class _FieldStats:
    occurrences: int = 0
    # Occurrences whose value was counted. Past the cap, a value never seen before is not, and
    # dividing by every occurrence instead would make any field look like it repeats itself.
    counted: int = 0
    value_counts: dict[str, int] = field(default_factory=dict)

    def record(self, value: str) -> None:
        self.occurrences += 1
        if value in self.value_counts or len(self.value_counts) < MAX_TRACKED_VALUES:
            self.value_counts[value] = self.value_counts.get(value, 0) + 1
            self.counted += 1

    @property
    def sample_coverage(self) -> float:
        """How often this field repeats itself, from 0 to 1.

        1.0: every value had been seen before. 0.0: every value was new, which is what an id is.

        Only reached once `identifies_the_request` has seen enough observations to ask.
        """
        singletons = sum(1 for count in self.value_counts.values() if count == 1)
        return 1 - singletons / self.counted

    @property
    def values(self) -> Iterable[str]:
        return self.value_counts.keys()

    @property
    def identifies_the_request(self) -> bool:
        if self.occurrences < MIN_OBSERVATIONS_TO_JUDGE:
            return False
        return self.sample_coverage < MIN_SAMPLE_COVERAGE


@dataclass
class BehaviorAlphabet:
    """Distinct behaviors observed for a single API operation."""

    counts: dict[SpeciesKey, _Seen] = field(default_factory=dict)
    # Responses recorded. Both estimates are built from this and the counts.
    total: int = 0
    _fields: dict[str, _FieldStats] = field(default_factory=dict)
    # Fields given up on at the cap. Unlike the usual rule, permanent.
    _forced: set[str] = field(default_factory=set)

    def record(self, *, status_code: int, body: bytes | None, headers: Mapping[str, list[str]] | None = None) -> None:
        candidates = self._labels(body) + self._header_labels(headers)
        labels = tuple(sorted((name, value) for name, value in candidates if name not in self._forced))
        for name, value in labels:
            self._fields.setdefault(name, _FieldStats()).record(value)
        self.total += 1
        key = (status_code, labels)
        seen = self.counts.get(key)
        if seen is None:
            self.counts[key] = _Seen(count=1, first=self.total)
        else:
            seen.count += 1
        if len(self.counts) > MAX_RETAINED_KEYS and self._fields:
            self._force_drop_widest_field()

    def seen_values(self, name: str) -> Iterable[str]:
        """Values recorded for one field, whether or not the field was judged to hold ids."""
        stats = self._fields.get(name)
        return stats.values if stats is not None else ()

    @property
    def distinct(self) -> int:
        return len(self._projected())

    @property
    def singletons(self) -> int:
        """Behaviors seen exactly once. Used to estimate how much is still unseen."""
        return sum(1 for seen in self._projected().values() if seen.count == 1)

    @property
    def discovery_probability(self) -> float:
        """How likely the next response is to show something this operation has not done yet.

        Estimated from the behaviors seen exactly once: many one-offs suggest more are out there.
        It never returns zero, so it can say "probably slow" but never "finished".

        Comparing two operations by it is unreliable below a few hundred responses each.
        """
        if not self.total:
            return 1.0
        singletons = self.singletons
        if singletons:
            return singletons / self.total
        return 1 / (self.total + 2)

    def richness(self) -> float:
        """A guess at how many behaviors this operation has, including unseen ones.

        It covers what our requests can reach, not what the service can do - behaviors needing
        input we never send stay invisible.

        Do not show it, and do not divide by it for a percentage. Such guesses run far too low
        until a run nears its ceiling, and ours never do.
        """
        counts = self._projected()
        if not self.total:
            return 0.0
        return len(counts) + self.singletons * (self.total - 1) / self.total

    def counts_snapshot(self) -> BehaviorCounts:
        """Every reported number in one pass, instead of six separate recomputations."""
        counts = self._projected()
        singletons = doubletons = 0
        discoveries = []
        for seen in counts.values():
            if seen.count == 1:
                singletons += 1
            elif seen.count == 2:
                doubletons += 1
            discoveries.append(seen.first)
        discoveries.sort()
        if not self.total:
            return BehaviorCounts(0, 0, 0, 0, 1.0, 0.0, [])
        return BehaviorCounts(
            responses=self.total,
            distinct=len(counts),
            singletons=singletons,
            doubletons=doubletons,
            discovery_probability=singletons / self.total if singletons else 1 / (self.total + 2),
            richness=len(counts) + singletons * (self.total - 1) / self.total,
            discoveries=discoveries,
        )

    def _dropped(self) -> set[str]:
        return {name for name, stats in self._fields.items() if stats.identifies_the_request} | self._forced

    def _projected(self) -> dict[SpeciesKey, _Seen]:
        """The behavior counts, with id-like fields left out.

        Judged here rather than on arrival: early on, twenty categories seen once each look exactly
        like twenty ids. Deciding late lets a field count again once its values start repeating.
        """
        dropped = self._dropped()
        if not dropped:
            return self.counts
        return self._without(self.counts, dropped)

    @staticmethod
    def _without(counts: dict[SpeciesKey, _Seen], dropped: set[str]) -> dict[SpeciesKey, _Seen]:
        merged: dict[SpeciesKey, _Seen] = {}
        for (status_code, labels), seen in counts.items():
            key = (status_code, tuple((name, value) for name, value in labels if name not in dropped))
            previous = merged.get(key)
            if previous is None:
                merged[key] = _Seen(count=seen.count, first=seen.first)
            else:
                # Behaviors that turn out to be one behavior were first seen whenever the earlier was.
                previous.count += seen.count
                previous.first = min(previous.first, seen.first)
        return merged

    def _force_drop_widest_field(self) -> None:
        widest = max(self._fields, key=lambda name: len(self._fields[name].value_counts))
        self._forced.add(widest)
        del self._fields[widest]
        self.counts = self._without(self.counts, {widest})

    def _header_labels(self, headers: Mapping[str, list[str]] | None) -> Labels:
        """What the headers might be saying - a redirect target, an auth scheme.

        Ones that change every response, like `Date`, are dropped later, as ids are.
        """
        if not headers:
            return ()
        return tuple(
            (f"{HEADER_PREFIX}{name}", values[0])
            for name, values in headers.items()
            if values and len(values[0]) <= MAX_LABEL_LENGTH
        )

    def _labels(self, body: bytes | None) -> Labels:
        """Everything this body might be saying, whatever its shape.

        Any of it may turn out to be an id; that is decided later.
        """
        if body is None or not body.strip():
            return ()
        try:
            payload = json.loads(body)
        except (ValueError, TypeError, UnicodeDecodeError):
            return ((TEXT, _normalize_text(body)),)
        if isinstance(payload, dict):
            return _object_labels(payload)
        if isinstance(payload, list):
            return ((ELEMENTS, _summarise_elements(payload)),)
        return ((VALUE, _as_label(payload)),)


@dataclass
class BehaviorCounts:
    """Everything a report needs about one operation."""

    responses: int
    distinct: int
    singletons: int
    doubletons: int
    discovery_probability: float
    richness: float
    # Which response first showed each behavior, in order. The curve a prediction of what more
    # budget buys has to be fitted to; keeping the points leaves the choice of fit to the reader.
    discoveries: list[int]

    @property
    def waited(self) -> int:
        """Responses since the last one that showed a behavior never seen before.

        An observation rather than an estimate, and the only one of the two that can report that
        nothing new has happened for a long time - `discovery_probability` never reaches zero. That
        is what a decision to stop spending on an operation needs.
        """
        return self.responses - self.discoveries[-1] if self.discoveries else self.responses


def _report(counts: BehaviorCounts, missed: dict[str, list[str]]) -> dict[str, object]:
    """One operation's row. The raw counts are enough to recompute the rest later."""
    if missed:
        return {**_counts_report(counts), "never_seen": missed}
    return _counts_report(counts)


def _counts_report(counts: BehaviorCounts) -> dict[str, object]:
    return {
        "distinct": counts.distinct,
        "estimated": round(counts.richness, 1),
        "discovery_probability": round(counts.discovery_probability, 4),
        "responses": counts.responses,
        "singletons": counts.singletons,
        "doubletons": counts.doubletons,
        "waited": counts.waited,
        "discoveries": counts.discoveries,
    }


@dataclass
class BehaviorSummary:
    """What a run observed, and under which definition of a behavior."""

    alphabet: str
    operations: dict[str, dict[str, object]]


@dataclass
class BehaviorCensus:
    """Behavior alphabets for every operation a run has exercised."""

    # A count means nothing without knowing how behaviors were told apart, so reports carry this.
    name: str = "status+labels"
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _by_operation: dict[str, BehaviorAlphabet] = field(default_factory=dict)
    _declared: dict[str, Mapping[str, frozenset[str]]] = field(default_factory=dict)

    def record(
        self,
        *,
        operation_label: str,
        status_code: int,
        body: bytes | None,
        headers: Mapping[str, list[str]] | None = None,
    ) -> None:
        with self._lock:
            alphabet = self._by_operation.setdefault(operation_label, BehaviorAlphabet())
            alphabet.record(status_code=status_code, body=body, headers=headers)

    def knows(self, operation_label: str) -> bool:
        """Whether this operation's declarations have already been collected."""
        with self._lock:
            return operation_label in self._declared

    def declare(self, operation_label: str, values: Mapping[str, frozenset[str]]) -> None:
        """Record which values this operation's schema says each of its response fields can hold.

        An enum in a response schema is the one ceiling we do not have to estimate: the values are
        written down. Everything else here counts what happened and guesses at what did not.

        An operation that declares nothing is recorded as such, so it is not walked again.
        """
        with self._lock:
            self._declared.setdefault(operation_label, values)

    def discovery_probability(self, operation_labels: Iterable[str] | None = None) -> float:
        """How likely the next response anywhere in the run is to show something new.

        Operations are averaged equally, since the run gives them equal time; a scheduler that
        spends unevenly should pass its own list. An operation that has not run counts as 1.0 -
        everything it does is still to be found.
        """
        with self._lock:
            labels = list(operation_labels) if operation_labels is not None else list(self._by_operation)
            if not labels:
                return 1.0
            alphabets = [self._by_operation.get(label) for label in labels]
            return sum(1.0 if item is None else item.discovery_probability for item in alphabets) / len(labels)

    def _missed(self, operation_label: str, alphabet: BehaviorAlphabet) -> dict[str, list[str]]:
        """Declared values this operation never produced, by field.

        A value can be declared and still be out of reach in a given deployment, so this points at
        something to look into - a missing link, absent test data - and never at a defect.
        """
        missed = {}
        for name, declared in self._declared.get(operation_label, {}).items():
            missing = sorted(declared - set(alphabet.seen_values(name)))
            if missing:
                missed[name] = missing
        return missed

    def summary(self) -> BehaviorSummary:
        """Behavior counts per operation, and the name of the rule behind them.

        The raw counts are enough to recompute the rest later. `estimated` is for analysis only -
        see `richness`.
        """
        with self._lock:
            return BehaviorSummary(
                alphabet=self.name,
                operations={
                    label: _report(alphabet.counts_snapshot(), self._missed(label, alphabet))
                    for label, alphabet in sorted(self._by_operation.items())
                },
            )
