from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.core.parameters import ParameterLocation

if TYPE_CHECKING:
    from schemathesis.generation.case import Case


_MIN_VALUE_LENGTH = 4

_BLOCKLIST: frozenset[str] = frozenset({"true", "false", "null"})

_MAX_DEPTH = 32
_MAX_NODES = 10_000


def _wire_form(value: object) -> str:
    """Render a value to the string the server saw on the wire.

    Strings pass through unchanged. Bytes have no text wire form and can never
    appear verbatim in a text error message, so they fold to an empty (never-matching)
    string. Everything else renders via JSON, matching the body wire format.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return ""
    return json.dumps(value)


@dataclass(slots=True)
class _WalkState:
    """Per-call walk budget; tracks remaining node capacity."""

    nodes_left: int


def _walk_body(
    body: object,
    *,
    rejected_value: str,
    state: _WalkState,
) -> Iterator[tuple[tuple[str | int, ...], object]]:
    """Yield every (path, value) where the value's wire form equals `rejected_value`."""
    stack: list[tuple[tuple[str | int, ...], object, int]] = [((), body, 0)]
    while stack and state.nodes_left > 0:
        path, current, depth = stack.pop()
        state.nodes_left -= 1
        if depth > _MAX_DEPTH:
            continue
        if isinstance(current, dict):
            for key, value in current.items():
                if isinstance(key, str):
                    stack.append(((*path, key), value, depth + 1))
        elif isinstance(current, list):
            for index, value in enumerate(current):
                stack.append(((*path, index), value, depth + 1))
        elif _wire_form(current) == rejected_value:
            yield path, current


def _walk_flat(
    container: Mapping[str, object],
    *,
    rejected_value: str,
    state: _WalkState,
) -> Iterator[tuple[tuple[str | int, ...], object]]:
    """Yield (path, value) for query / headers / cookies / path_parameters."""
    for key, value in container.items():
        if state.nodes_left == 0:
            return
        state.nodes_left -= 1
        if isinstance(key, str) and _wire_form(value) == rejected_value:
            yield (key,), value


def infer_path_from_request(
    *,
    case: Case,
    rejected_value: str,
) -> tuple[ParameterLocation, tuple[str | int, ...]] | None:
    """Find the parameter slot whose wire-form value equals `rejected_value`.

    Returns the single matching slot or `None` when the value is too low-entropy
    to attribute, no candidate matches, or candidates are ambiguous.
    """
    if rejected_value in _BLOCKLIST:
        return None
    if len(rejected_value) < _MIN_VALUE_LENGTH:
        return None

    state = _WalkState(nodes_left=_MAX_NODES)
    candidates: list[tuple[ParameterLocation, tuple[str | int, ...]]] = []

    body = case.body
    if isinstance(body, (dict, list)):
        for path, _ in _walk_body(body, rejected_value=rejected_value, state=state):
            candidates.append((ParameterLocation.BODY, path))

    # Body is walked recursively above. Non-body containers match by top-level
    # value only, so nested parameter styles (deepObject, pipeDelimited, exploded
    # form-object, etc.) attribute to the top-level parameter or not at all.
    for container, location in (
        (case.query, ParameterLocation.QUERY),
        (case.path_parameters, ParameterLocation.PATH),
        (case.headers, ParameterLocation.HEADER),
        (case.cookies, ParameterLocation.COOKIE),
    ):
        if not container:
            continue
        for path, _ in _walk_flat(container, rejected_value=rejected_value, state=state):
            candidates.append((location, path))

    if len(candidates) == 1:
        return candidates[0]
    return None
