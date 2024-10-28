from __future__ import annotations

from shlex import quote
from typing import TYPE_CHECKING

from .transports import get_excluded_headers

if TYPE_CHECKING:
    from .types import Headers


def generate(
    *,
    method: str,
    url: str,
    body: str | bytes | None,
    headers: Headers | None,
    verify: bool,
    extra_headers: Headers | None = None,
) -> str:
    """Generate a code snippet for making HTTP requests."""
    headers = _filter_headers(headers, extra_headers)
    command = f"curl -X {method}"
    for key, value in headers.items():
        header = f"{key}: {value}"
        command += f" -H {quote(header)}"
    if body:
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        command += f" -d {quote(body)}"
    if not verify:
        command += " --insecure"
    return f"{command} {quote(url)}"


def _filter_headers(headers: Headers | None, extra: Headers | None = None) -> Headers:
    headers = headers.copy() if headers else {}
    if extra is not None:
        for key, value in extra.items():
            if key not in get_excluded_headers():
                headers[key] = value
    return headers
