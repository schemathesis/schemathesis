from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, TypeGuard

from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.parsers.extractors import (
    ClassificationResult,
    RegexHandler,
    location_for_method,
    numeric_bound,
    size_bound,
    size_bound_exact,
)
from schemathesis.core.error_feedback.store import (
    BoundDirection,
    Observation,
    ObservationKind,
    TypeMismatchPayload,
)

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation

WalkPair = tuple[tuple[str | int, ...], str]
ModernShape = Mapping[str, Sequence[object]]
LegacyShape = Sequence[str]

# Object-level (cross-field) errors have no attribute to adjust against, so skip them.
_BASE_KEY = "base"

# Tokens every Rails default phrasing starts with. The legacy-form splitter
# treats everything before the first match as the humanised attribute name
# and the rest as the message body.
_LEAD_TOKENS = ("can't", "is", "must", "doesn't", "has", "should", "may", "are")
_LEAD_PATTERN = re.compile(r"\b(" + "|".join(re.escape(t) for t in _LEAD_TOKENS) + r")\b")

# Vocabulary discriminator — substrings that, if found in any message, lock
# detection to Rails. Disambiguates from sibling parsers whose envelope shape
# overlaps (dict-of-lists with field names as keys) but whose vocabulary differs.
_RAILS_VOCABULARY: frozenset[str] = frozenset(
    {
        "can't be blank",
        "is invalid",
        "is not a number",
        "must be an integer",
        "is not included in the list",
        "is reserved",
        "must be accepted",
        "is too short (minimum is",
        "is too long (maximum is",
        "is the wrong length (should be",
        "must be greater than",
        "must be less than",
        "must be equal to",
        "must be other than",
        "must be even",
        "must be odd",
        "doesn't match",
        "has already been taken",
        "must be filled",
    }
)


def _walk_modern(body: ModernShape, path: tuple[str | int, ...] = ()) -> Iterator[WalkPair]:
    """Walk the modern `errors.as_json` shape — dict-of-lists of strings.

    Also handles the dotted-key form Rails emits for nested attributes
    (`{"address.street": ["can't be blank"]}`) by splitting the key on `.`.
    """
    for raw_key, value in body.items():
        if not isinstance(raw_key, str) or raw_key == _BASE_KEY:
            continue
        # Rails dotted-key for nested attributes; split into segments.
        key_path: tuple[str | int, ...] = tuple(raw_key.split(".")) if "." in raw_key else (raw_key,)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str) and item:
                yield (path + key_path, item)


def _split_legacy_message(line: str) -> tuple[str, str] | None:
    """Strip the humanised field name prefix from a `full_messages` entry.

    Rails formats each entry as `f"{attribute.humanize} {message}"` — for
    `:terms_accepted` the humanised form is `"Terms accepted"`, with a space
    between the original underscore-separated tokens. We recover the field
    name by taking everything up to the first lead token (`can't`, `is`,
    `must`, `doesn't`, ...), lowercasing, and joining with underscores.

    Returns `(snake_case_field, message)` or `None` if no lead token is found.
    """
    match = _LEAD_PATTERN.search(line)
    if match is None:
        return None
    head = line[: match.start()].rstrip()
    if not head:
        return None
    return "_".join(head.split()).lower(), line[match.start() :]


def _walk_legacy(messages: LegacyShape, path: tuple[str | int, ...] = ()) -> Iterator[WalkPair]:
    """Walk the legacy `full_messages` shape — flat list of humanised strings."""
    for line in messages:
        if not isinstance(line, str) or not line:
            continue
        split = _split_legacy_message(line)
        if split is None:
            continue
        field, message = split
        if field == _BASE_KEY:
            continue
        yield (path + (field,), message)


_LITERAL_MESSAGES: dict[str, ClassificationResult] = {
    "can't be blank": (ObservationKind.MUST_NOT_BE_BLANK, None),
    "can't be empty": (ObservationKind.MUST_NOT_BE_BLANK, None),
    "must be filled": (ObservationKind.MUST_NOT_BE_BLANK, None),
    "must exist": (ObservationKind.MUST_NOT_BE_BLANK, None),
    "is not a number": (
        ObservationKind.TYPE_MISMATCH,
        TypeMismatchPayload(type_name="number"),
    ),
    "must be an integer": (
        ObservationKind.TYPE_MISMATCH,
        TypeMismatchPayload(type_name="integer"),
    ),
}


_REGEX_PATTERNS: tuple[tuple[re.Pattern[str], RegexHandler], ...] = (
    # `is too short (minimum is N character(s))` — Rails uses `characters`
    # for both string and array length, so a single pattern covers both.
    (
        re.compile(r"^is too short \(minimum is (\d+) characters?\)$"),
        size_bound(direction=BoundDirection.MIN),
    ),
    (
        re.compile(r"^is too long \(maximum is (\d+) characters?\)$"),
        size_bound(direction=BoundDirection.MAX),
    ),
    (
        re.compile(r"^is the wrong length \(should be (\d+) characters?\)$"),
        size_bound_exact(),
    ),
    # Numericality bounds. Rails formats integers without decimals
    # (`must be greater than 0`) and floats with (`must be greater than 0.0`).
    (
        re.compile(r"^must be greater than or equal to (-?\d+(?:\.\d+)?)$"),
        numeric_bound(direction=BoundDirection.MIN, exclusive=False),
    ),
    (
        re.compile(r"^must be greater than (-?\d+(?:\.\d+)?)$"),
        numeric_bound(direction=BoundDirection.MIN, exclusive=True),
    ),
    (
        re.compile(r"^must be less than or equal to (-?\d+(?:\.\d+)?)$"),
        numeric_bound(direction=BoundDirection.MAX, exclusive=False),
    ),
    (
        re.compile(r"^must be less than (-?\d+(?:\.\d+)?)$"),
        numeric_bound(direction=BoundDirection.MAX, exclusive=True),
    ),
)


def _classify_message(message: str) -> ClassificationResult | None:
    """Return the (kind, payload) for `message`, or None when no JSON-Schema constraint maps to it."""
    if message in _LITERAL_MESSAGES:
        return _LITERAL_MESSAGES[message]
    for pattern, handler in _REGEX_PATTERNS:
        match = pattern.match(message)
        if match is not None:
            return handler(match)
    return None


def _has_rails_vocabulary(messages: Iterator[str]) -> bool:
    """Return True when any message contains a Rails-only canonical phrasing."""
    return any(phrase in message for message in messages for phrase in _RAILS_VOCABULARY)


def _is_modern_shape(body: object) -> TypeGuard[ModernShape]:
    """Modern: top-level dict whose values are lists, with at least one non-empty list of strings."""
    if not isinstance(body, dict) or not body:
        return False
    found_list_of_strings = False
    for value in body.values():
        if not isinstance(value, list):
            return False
        if not value:
            continue
        if not all(isinstance(item, str) for item in value):
            return False
        found_list_of_strings = True
    return found_list_of_strings


def _is_legacy_shape(messages: object) -> TypeGuard[LegacyShape]:
    """Legacy: list of non-empty strings (the `full_messages` form)."""
    if not isinstance(messages, list) or not messages:
        return False
    return all(isinstance(item, str) and item for item in messages)


def _unwrap_ar_envelope(body: object) -> object:
    """Strip the `{"errors": <inner>, ...}` wrapper if present, return inner; otherwise return body unchanged."""
    if isinstance(body, dict) and "errors" in body and len(body) <= 2:
        return body["errors"]
    return body


def _walk_observations(body: object) -> Iterator[WalkPair]:
    """Yield `(path, message)` pairs for whichever envelope shape `body` matches; empty otherwise."""
    if _is_modern_shape(body):
        yield from _walk_modern(body)
    elif _is_legacy_shape(body):
        yield from _walk_legacy(body)


@PARSERS.register
class RailsParser:
    """Parser for Rails `ActiveModel::Errors` envelopes.

    Accepts three response shapes:
    - Modern `as_json` — `{"<field>": ["<message>", ...], ...}`
    - Legacy `full_messages` — `["<Humanised Field> <message>", ...]`
    - AR-wrapped — `{"errors": <one of the above>}`
    """

    priority = 4

    def can_parse(self, *, body: object) -> bool:
        inner = _unwrap_ar_envelope(body)
        messages = (message for _, message in _walk_observations(inner))
        return _has_rails_vocabulary(messages)

    def parse(self, *, operation: APIOperation, body: object) -> tuple[Observation, ...]:
        location = location_for_method(operation.method)
        observations: list[Observation] = []
        for path, message in _walk_observations(_unwrap_ar_envelope(body)):
            classification = _classify_message(message)
            if classification is None:
                continue
            kind, payload = classification
            observations.append(
                Observation(
                    operation_label=operation.label,
                    location=location,
                    parameter_path=path,
                    kind=kind,
                    raw_message=message,
                    payload=payload,
                )
            )
        return tuple(observations)
