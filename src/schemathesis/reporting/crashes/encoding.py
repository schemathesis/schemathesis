from __future__ import annotations

import base64
from typing import Any

from schemathesis.core import NOT_SET

_BYTES_TAG = "__schemathesis_bytes__"
_ESCAPE_TAG = "__schemathesis_escaped__"


def to_json_safe(value: Any) -> Any:
    """Make a generated parameter value JSON-serializable: bytes -> base64 tag."""
    if isinstance(value, bytes):
        return {_BYTES_TAG: base64.b64encode(value).decode()}
    if isinstance(value, dict):
        encoded = {key: to_json_safe(item) for key, item in value.items()}
        # Escape real data that collides with a reserved single-key marker.
        if set(encoded) in ({_BYTES_TAG}, {_ESCAPE_TAG}):
            return {_ESCAPE_TAG: encoded}
        return encoded
    if isinstance(value, (list, tuple)):
        return [to_json_safe(item) for item in value]
    return value


def from_json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        keys = set(value)
        if keys == {_BYTES_TAG}:
            return base64.b64decode(value[_BYTES_TAG])
        if keys == {_ESCAPE_TAG}:
            return {key: from_json_safe(item) for key, item in value[_ESCAPE_TAG].items()}
        return {key: from_json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [from_json_safe(item) for item in value]
    return value


def encode_case_body(body: Any) -> dict[str, Any]:
    if body is NOT_SET:
        return {"encoding": "none"}
    if isinstance(body, bytes):
        return {"encoding": "base64", "value": base64.b64encode(body).decode()}
    return {"encoding": "json", "value": to_json_safe(body)}


def decode_case_body(data: dict[str, Any]) -> Any:
    encoding = data["encoding"]
    if encoding == "none":
        return NOT_SET
    if encoding == "base64":
        return base64.b64decode(data["value"])
    return from_json_safe(data["value"])
