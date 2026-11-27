"""Counting the distinct behaviors of each API operation, separately for every source of evidence."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable, Hashable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.core.transport import Response

# Deeper values are not descended into.
MAX_DEPTH = 8
# Elements of a list share their structure, so the first few stand for the rest.
MAX_LIST_ITEMS = 8
# Names for bodies that are not JSON objects or arrays.
TEXT = "<text>"
VALUE = "<value>"
# Path segment for list elements.
ELEMENT = "[]"
# Enough of a failure message to tell two apart without a stack trace making every response unique.
TEXT_PREFIX_LENGTH = 120
# A message is read this far, so a long stack trace costs no more than a short one.
MAX_MESSAGE_BYTES = 1024

# A quote opens after a non-word character and closes before one, so `'text' or 'data'` is two spans
# and `'ab'cd'` is one.
QUOTED = re.compile(r"(?<!\w)'.*?'(?!\w)|(?<!\w)\".*?\"(?!\w)")
HEX = re.compile(r"\b0x[0-9a-fA-F]+\b")
# A minus is a sign only where it does not join two words or numbers: `-1` but not `1-10`.
NUMBERS = re.compile(r"(?<!\w)-?\d+|\d+")
WHITESPACE = re.compile(r"\s+")

Path = tuple[str, ...]


@dataclass(slots=True)
class Exchange:
    """What one request and its response tell about the operation."""

    response: Response
    # Declared query parameters the request carried.
    query_parameters: frozenset[str]


@dataclass(slots=True, frozen=True)
class Source:
    """One kind of evidence. Sources are counted apart, so their numbers are never added together."""

    name: str
    # Features past this many per operation are ignored: a source that never stops finding new ones tells nothing.
    limit: int
    features: Callable[[Exchange], Iterable[Hashable]]


def _mask(text: str) -> str:
    """Replace what varies inside a message while its wording stays the same."""
    return NUMBERS.sub("N", HEX.sub("<hex>", QUOTED.sub("<q>", text)))


def _paths(value: object, path: Path, depth: int) -> Iterator[Path]:
    if path:
        yield path
    if depth == MAX_DEPTH:
        return
    if isinstance(value, dict):
        for name, item in value.items():
            yield from _paths(item, (*path, name), depth + 1)
    elif isinstance(value, list):
        for item in value[:MAX_LIST_ITEMS]:
            yield from _paths(item, (*path, ELEMENT), depth + 1)


def _status(exchange: Exchange) -> Iterator[Hashable]:
    yield exchange.response.status_code


def _structure(exchange: Exchange) -> Iterator[Hashable]:
    response = exchange.response
    if not response.content or not response.content.strip():
        return
    try:
        # Parsed apart from the response's own cache: checks read that cache through custom deserializers.
        payload = json.loads(response.content)
    except (ValueError, RecursionError):
        yield (response.status_code, (TEXT,))
        return
    if isinstance(payload, (dict, list)):
        for path in _paths(payload, (), 0):
            yield (response.status_code, path)
    else:
        yield (response.status_code, (VALUE,))


def _failure(exchange: Exchange) -> Iterator[Hashable]:
    response = exchange.response
    if response.status_code >= 500 and response.content:
        # Masked before it is shortened, so masks do not shift the cut.
        text = _mask(WHITESPACE.sub(" ", response.content[:MAX_MESSAGE_BYTES].decode("utf-8", "replace")))
        message = text[:TEXT_PREFIX_LENGTH].strip()
        if message:
            yield (response.status_code, message)


def _parameters(exchange: Exchange) -> Iterator[Hashable]:
    # A refused request says nothing about which parameters the operation acts on.
    if 200 <= exchange.response.status_code < 300:
        yield exchange.query_parameters


SOURCES = (
    Source(name="status", limit=64, features=_status),
    Source(name="structure", limit=256, features=_structure),
    Source(name="failure", limit=8, features=_failure),
    Source(name="parameters", limit=16, features=_parameters),
)


@dataclass(slots=True)
class _Seen:
    # Responses containing the feature.
    count: int
    # Number of the response that first contained it, starting from 1.
    first: int


@dataclass(slots=True)
class SourceCounts:
    """Raw counts of one source for one operation."""

    # Distinct features.
    distinct: int
    # Features contained in exactly one response, and in exactly two.
    singletons: int
    doubletons: int
    # Responses containing at least one singleton.
    discovering: int
    # Number of the response that first contained each feature, in ascending order.
    discoveries: list[int]


@dataclass(slots=True)
class BehaviorCounts:
    """Raw counts for one operation."""

    responses: int
    # Sources that found nothing are left out.
    sources: dict[str, SourceCounts]


@dataclass(slots=True)
class BehaviorAlphabet:
    """Distinct features of the responses of one operation, by source."""

    responses: int = 0
    _features: dict[str, dict[Hashable, _Seen]] = field(default_factory=dict)

    def record(self, exchange: Exchange) -> None:
        self.responses += 1
        for source in SOURCES:
            self.add(source.name, source.features(exchange), limit=source.limit)

    def add(self, source: str, features: Iterable[Hashable], *, limit: int) -> None:
        """Count the features one source found in the latest response."""
        seen_by_source = self._features.get(source)
        for feature in set(features):
            if seen_by_source is None:
                seen_by_source = self._features[source] = {}
            seen = seen_by_source.get(feature)
            if seen is not None:
                seen.count += 1
            elif len(seen_by_source) < limit:
                seen_by_source[feature] = _Seen(count=1, first=self.responses)

    def counts(self) -> BehaviorCounts:
        sources = {}
        for name, features in self._features.items():
            singletons = doubletons = 0
            discovering = set()
            for seen in features.values():
                if seen.count == 1:
                    singletons += 1
                    discovering.add(seen.first)
                elif seen.count == 2:
                    doubletons += 1
            sources[name] = SourceCounts(
                distinct=len(features),
                singletons=singletons,
                doubletons=doubletons,
                discovering=len(discovering),
                discoveries=sorted(seen.first for seen in features.values()),
            )
        return BehaviorCounts(responses=self.responses, sources=sources)


@dataclass(slots=True)
class BehaviorCensus:
    """Behavior alphabets of every operation that received a response."""

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _alphabets: dict[str, BehaviorAlphabet] = field(default_factory=dict)

    def record(self, *, operation_label: str, exchange: Exchange) -> None:
        with self._lock:
            self._alphabets.setdefault(operation_label, BehaviorAlphabet()).record(exchange)

    def counts(self) -> dict[str, BehaviorCounts]:
        with self._lock:
            return {label: alphabet.counts() for label, alphabet in sorted(self._alphabets.items())}
