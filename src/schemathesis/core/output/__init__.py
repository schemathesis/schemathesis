from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from schemathesis.config import OutputConfig
    from schemathesis.core.transport import Response

TRUNCATED = "// Output truncated..."
EMPTY_PAYLOAD = "<EMPTY>"
BINARY_PAYLOAD = "<BINARY>"


def pluralize(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def escape_surrogates(text: str) -> str:
    # Lone surrogates (e.g. echoed by a server) cannot be UTF-8 encoded for output; escape them instead of crashing.
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def escape_nonprintable(text: str) -> str:
    # Escapes control characters like `repr` does, but keeps backslashes single.
    return "".join(char if char.isprintable() else repr(char)[1:-1] for char in text)


def truncate_json(data: Any, *, config: OutputConfig, max_lines: int | None = None) -> str:
    indent = 4
    if not config.truncation.enabled:
        return json.dumps(data, indent=indent)

    max_lines = max_lines if max_lines is not None else config.truncation.max_lines
    max_width = config.truncation.max_width
    # Encode incrementally and stop at enough lines: a large schema serializes to megabytes, almost all discarded.
    lines: list[str] = []
    pending = ""
    exhausted = True
    for chunk in json.JSONEncoder(indent=indent).iterencode(data):
        pending += chunk
        if "\n" not in pending:
            continue
        *ready, pending = pending.split("\n")
        lines.extend(ready)
        if len(lines) > max_lines:
            exhausted = False
            break
    if exhausted:
        lines.append(pending)
    lines = [line[: max_width - 3] + "..." if len(line) > max_width else line for line in lines]

    if exhausted and len(lines) <= max_lines:
        return "\n".join(lines)

    truncated_lines = lines[: max_lines - 1]
    indentation = " " * indent
    truncated_lines.append(f"{indentation}{TRUNCATED}")
    # The dropped tail always ends with the top-level container's closing delimiter.
    truncated_lines.append("]" if isinstance(data, list) else "}")

    return "\n".join(truncated_lines)


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 4] + " ..."


def decode_response_text(response: Response) -> str | None:
    """Response body for display, or `None` when binary; an unknown or broken charset decodes lossily."""
    if response.render_content is not None:
        return response.render_content(response.content)
    try:
        return response.text
    except UnicodeDecodeError as exc:
        # A body kept as a prefix can end inside a character; only that cut is undecodable.
        if response.is_truncated and exc.end == len(response.content):
            return response.content[: exc.start].decode(exc.encoding)
        return None
    except (LookupError, ValueError):
        # Unknown or broken codec names (including embedded NULs) decode lossily
        return response.text_lossy()


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


def format_response_payload(response: Response, *, config: OutputConfig) -> str:
    """Response body as failure output shows it: `<EMPTY>`, `<BINARY>`, or the decoded and truncated text."""
    if not response.content and response.render_content is None:
        return EMPTY_PAYLOAD
    text = decode_response_text(response)
    if text is None:
        return BINARY_PAYLOAD
    return prepare_response_payload(text, config=config)
