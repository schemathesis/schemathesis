from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.core.error_feedback.store import ParameterPath
from schemathesis.core.parameters import ParameterLocation

if TYPE_CHECKING:
    from schemathesis.generation.case import Case


_MIN_VALUE_LENGTH = 4

_BLOCKLIST: frozenset[str] = frozenset({"true", "false", "null"})

_MAX_DEPTH = 32
_MAX_NODES = 10_000


def _wire_form(value: object) -> str:
    """The value as the server saw it: strings as is, other values as JSON, bytes as `""` so they never match."""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return ""
    return json.dumps(value)


@dataclass(slots=True)
class _WalkState:
    nodes_left: int


def _walk_body(
    body: object,
    *,
    rejected_value: str,
    state: _WalkState,
) -> Iterator[tuple[ParameterPath, object]]:
    """Yield `(path, value)` for every leaf whose wire form equals `rejected_value`."""
    stack: list[tuple[ParameterPath, object, int]] = [((), body, 0)]
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
) -> Iterator[tuple[ParameterPath, object]]:
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
) -> tuple[ParameterLocation, ParameterPath] | None:
    """The one parameter slot whose wire form equals `rejected_value`.

    `None` when the value is too short or common to attribute, or matches no slot or several.
    """
    if rejected_value in _BLOCKLIST:
        return None
    if len(rejected_value) < _MIN_VALUE_LENGTH:
        return None

    state = _WalkState(nodes_left=_MAX_NODES)
    candidates: list[tuple[ParameterLocation, ParameterPath]] = []

    body = case.body
    if isinstance(body, (dict, list)):
        for path, _ in _walk_body(body, rejected_value=rejected_value, state=state):
            candidates.append((ParameterLocation.BODY, path))

    # Other locations match top-level values only: deepObject and similar styles map to the whole parameter or nothing.
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
