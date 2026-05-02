from __future__ import annotations

import json
from collections.abc import Iterator


def iter_ids_from_response(response_body: bytes, *, field_name: str) -> Iterator[str]:
    """Yield string `id` values found at `data[field_name]` in a GraphQL response.

    Handles both single-object (`{"id": ...}`) and list (`[{"id": ...}, ...]`) shapes.
    Yields nothing if the body is malformed, contains errors, or the path is missing.
    """
    try:
        payload = json.loads(response_body)
    except (ValueError, TypeError):
        return
    if not isinstance(payload, dict):
        return
    if payload.get("errors"):
        return
    data = payload.get("data")
    if not isinstance(data, dict):
        return
    field_value = data.get(field_name)
    if isinstance(field_value, dict):
        candidate = field_value.get("id")
        if isinstance(candidate, str):
            yield candidate
    elif isinstance(field_value, list):
        for item in field_value:
            if isinstance(item, dict):
                candidate = item.get("id")
                if isinstance(candidate, str):
                    yield candidate
