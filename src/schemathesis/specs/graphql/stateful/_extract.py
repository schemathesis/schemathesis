from __future__ import annotations

import json
from collections.abc import Iterator


def iter_handle_values(
    response_body: bytes, *, field_name: str, handle_fields: frozenset[str], path: tuple[str, ...] = ()
) -> Iterator[tuple[str, str]]:
    """Yield `(handle_field, value)` pairs of records found at `data[field_name]` and then `path` in a GraphQL response.

    Lists at any step are flattened, so `path=("edges", "node")` reads Relay connections.
    Yields nothing if the body is malformed, contains errors, or the path is missing.
    """
    try:
        payload = json.loads(response_body)
    except (ValueError, TypeError):
        return
    if not isinstance(payload, dict) or payload.get("errors"):
        return
    data = payload.get("data")
    if not isinstance(data, dict):
        return
    for record in _records(data.get(field_name), path):
        for handle_field in handle_fields:
            candidate = record.get(handle_field)
            if isinstance(candidate, str):
                yield handle_field, candidate


def _records(value: object, path: tuple[str, ...]) -> Iterator[dict]:
    if isinstance(value, list):
        for item in value:
            yield from _records(item, path)
    elif isinstance(value, dict):
        if path:
            yield from _records(value.get(path[0]), path[1:])
        else:
            yield value
