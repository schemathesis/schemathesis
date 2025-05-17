from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from schemathesis.config import OutputConfig

TRUNCATED = "// Output truncated..."


def truncate_json(data: Any, *, config: OutputConfig, max_lines: int | None = None) -> str:
    # Convert JSON to string with indentation
    indent = 4
    serialized = json.dumps(data, indent=indent)
    if not config.truncation.enabled:
        return serialized

    max_lines = max_lines if max_lines is not None else config.truncation.max_lines
    # Split string by lines
    lines = [
        line[: config.truncation.max_width - 3] + "..." if len(line) > config.truncation.max_width else line
        for line in serialized.split("\n")
    ]

    if len(lines) <= max_lines:
        return "\n".join(lines)

    truncated_lines = lines[: max_lines - 1]
    indentation = " " * indent
    truncated_lines.append(f"{indentation}{TRUNCATED}")
    truncated_lines.append(lines[-1])

    return "\n".join(truncated_lines)


def prepare_response_payload(payload: str, *, config: OutputConfig) -> str:
    if payload.endswith("\r\n"):
        payload = payload[:-2]
    elif payload.endswith("\n"):
        payload = payload[:-1]
    if not config.truncation.enabled:
        return payload
    if len(payload) > config.truncation.max_payload_size:
        payload = payload[: config.truncation.max_payload_size] + f" {TRUNCATED}"
    return payload
