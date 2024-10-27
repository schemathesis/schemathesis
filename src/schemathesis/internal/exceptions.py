"""Working with tracebacks and exceptions."""

from __future__ import annotations

import re
import traceback
from types import TracebackType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from requests import RequestException


def format_exception(
    error: Exception,
    *,
    with_traceback: bool = False,
    skip_frames: int = 0,
) -> str:
    """Format exception with optional traceback."""
    if not with_traceback:
        lines = traceback.format_exception_only(type(error), error)
        return "".join(lines).strip()

    trace = error.__traceback__
    if skip_frames > 0:
        trace = extract_nth_traceback(trace, skip_frames)
    lines = traceback.format_exception(type(error), error, trace)
    return "".join(lines).strip()


def split_traceback(traceback: str) -> list[str]:
    return [entry for entry in traceback.splitlines() if entry]


def extract_nth_traceback(trace: TracebackType | None, n: int) -> TracebackType | None:
    depth = 0
    while depth < n and trace is not None:
        trace = trace.tb_next
        depth += 1
    return trace


def get_request_error_message(exc: RequestException) -> str:
    """Extract user-facing message from a request exception."""
    from requests.exceptions import ChunkedEncodingError, ConnectionError, SSLError

    if isinstance(exc, SSLError):
        return "SSL verification problem"
    if isinstance(exc, ConnectionError):
        return "Connection failed"
    if isinstance(exc, ChunkedEncodingError):
        return "Connection broken. The server declared chunked encoding but sent an invalid chunk"
    return str(exc)


def get_request_error_extras(exc: RequestException) -> list[str]:
    """Extract additional context from a request exception."""
    from requests.exceptions import ChunkedEncodingError, ConnectionError, SSLError
    from urllib3.exceptions import MaxRetryError

    if isinstance(exc, SSLError):
        reason = str(exc.args[0].reason)
        return [_remove_ssl_line_number(reason).strip()]
    if isinstance(exc, ConnectionError):
        inner = exc.args[0]
        if isinstance(inner, MaxRetryError) and inner.reason is not None:
            arg = inner.reason.args[0]
            if isinstance(arg, str):
                if ":" not in arg:
                    reason = arg
                else:
                    _, reason = arg.split(":", maxsplit=1)
            else:
                reason = f"Max retries exceeded with url: {inner.url}"
            return [reason.strip()]
        return [" ".join(map(_clean_inner_request_message, inner.args))]
    if isinstance(exc, ChunkedEncodingError):
        return [str(exc.args[0].args[1])]
    return []


def _remove_ssl_line_number(text: str) -> str:
    return re.sub(r"\(_ssl\.c:\d+\)", "", text)


def _clean_inner_request_message(message: object) -> str:
    if isinstance(message, str) and message.startswith("HTTPConnectionPool"):
        return re.sub(r"HTTPConnectionPool\(.+?\): ", "", message).rstrip(".")
    return str(message)
