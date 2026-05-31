from __future__ import annotations

import json
from collections.abc import Iterator


def iter_handle_values(
    response_body: bytes, *, field_name: str, handle_fields: frozenset[str]
) -> Iterator[tuple[str, str]]:
    """Yield `(handle_field, value)` pairs found at `data[field_name]` in a GraphQL response.

    Handles single-object, list, and Relay connection (`edges { node }`) shapes.
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
    for record in _records(data.get(field_name)):
        for handle_field in handle_fields:
            candidate = record.get(handle_field)
            if isinstance(candidate, str):
                yield handle_field, candidate


def _records(field_value: object) -> Iterator[dict]:
    if isinstance(field_value, dict):
        edges = field_value.get("edges")
        if isinstance(edges, list):
            for edge in edges:
                if isinstance(edge, dict) and isinstance(edge.get("node"), dict):
                    yield edge["node"]
        else:
            yield field_value
    elif isinstance(field_value, list):
        for item in field_value:
            if isinstance(item, dict):
                yield item
