from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

TRUNCATED = "// Output truncated..."
MAX_PAYLOAD_SIZE = 512
MAX_LINES = 10
MAX_WIDTH = 80


@dataclass
class OutputConfig:
    """Options for configuring various aspects of Schemathesis output."""

    truncate: bool = True
    max_payload_size: int = MAX_PAYLOAD_SIZE
    max_lines: int = MAX_LINES
    max_width: int = MAX_WIDTH

    @classmethod
    def from_parent(cls, parent: OutputConfig | None = None, **changes: Any) -> OutputConfig:
        parent = parent or OutputConfig()
        return parent.replace(**changes)

    def replace(self, **changes: Any) -> OutputConfig:
        """Create a new instance with updated values."""
        return replace(self, **changes)


def truncate_json(data: Any, *, config: OutputConfig | None = None) -> str:
    config = config or OutputConfig()
    # Convert JSON to string with indentation
    indent = 4
    serialized = json.dumps(data, indent=indent)
    if not config.truncate:
        return serialized

    # Split string by lines

    lines = [
        line[: config.max_width - 3] + "..." if len(line) > config.max_width else line
        for line in serialized.split("\n")
    ]

    if len(lines) <= config.max_lines:
        return "\n".join(lines)

    truncated_lines = lines[: config.max_lines - 1]
    indentation = " " * indent
    truncated_lines.append(f"{indentation}{TRUNCATED}")
    truncated_lines.append(lines[-1])

    return "\n".join(truncated_lines)


def prepare_response_payload(payload: str, *, config: OutputConfig | None = None) -> str:
    if payload.endswith("\r\n"):
        payload = payload[:-2]
    elif payload.endswith("\n"):
        payload = payload[:-1]
    config = config or OutputConfig()
    if not config.truncate:
        return payload
    if len(payload) > config.max_payload_size:
        payload = payload[: config.max_payload_size] + f" {TRUNCATED}"
    return payload
