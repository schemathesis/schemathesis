from __future__ import annotations

import json
from collections.abc import Iterator

from schemathesis.core.transforms import encode_pointer


def iter_handle_values(
    response_body: bytes, *, field_name: str, handle_fields: frozenset[str], path: tuple[str, ...] = ()
) -> Iterator[tuple[str, str, str]]:
    """Yield `(handle_field, value, pointer)` of records found at `data[field_name]` and then `path` in a GraphQL response.

    Lists at any step are flattened, so `path=("edges", "node")` reads Relay connections. `pointer` is the JSON Pointer
    to the value in the response. Yields nothing if the body is malformed, contains errors, or the path is missing.
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
    for pointer, record in _records(data.get(field_name), path, f"/data/{encode_pointer(field_name)}"):
        for handle_field in handle_fields:
            candidate = record.get(handle_field)
            if isinstance(candidate, str):
                yield handle_field, candidate, f"{pointer}/{encode_pointer(handle_field)}"


def _records(value: object, path: tuple[str, ...], pointer: str) -> Iterator[tuple[str, dict]]:
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _records(item, path, f"{pointer}/{index}")
    elif isinstance(value, dict):
        if path:
            yield from _records(value.get(path[0]), path[1:], f"{pointer}/{encode_pointer(path[0])}")
        else:
            yield pointer, value
