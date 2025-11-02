from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from shlex import quote
from typing import TYPE_CHECKING, Any

from schemathesis.core import SCHEMATHESIS_TEST_CASE_HEADER
from schemathesis.core.shell import escape_for_shell, has_non_printable

if TYPE_CHECKING:
    from requests.models import CaseInsensitiveDict


@dataclass(frozen=True)
class CurlCommand:
    """Result of generating a curl command."""

    command: str
    """The curl command string."""

    warnings: list[str]
    """Warnings about non-printable characters or shell compatibility."""

    __slots__ = ("command", "warnings")


def _escape_and_quote(value: str, warnings: list[str], ctx: str) -> str:
    """Escape value for shell, adding warnings if needed."""
    if has_non_printable(value):
        escape_result = escape_for_shell(value)
        if escape_result.needs_warning:
            warnings.append(f"{ctx} contains non-printable characters. Actual value: {escape_result.original_bytes!r}")
        return escape_result.escaped_value
    return quote(value)


def generate(
    *,
    method: str,
    url: str,
    body: str | bytes | None,
    verify: bool,
    headers: dict[str, Any],
    known_generated_headers: dict[str, Any] | None,
) -> CurlCommand:
    """Generate a code snippet for making HTTP requests."""
    _filter_headers(headers, known_generated_headers or {})
    warnings: list[str] = []
    command = f"curl -X {method}"

    # Process headers with shell-aware escaping
    for key, value in headers.items():
        # To send an empty header with cURL we need to use `;`, otherwise empty header is ignored
        if not value:
            header = f"{key};"
        else:
            header = f"{key}: {value}"

        escaped_header = _escape_and_quote(header, warnings, f"Header '{key}'")
        command += f" -H {escaped_header}"

    # Process body with shell-aware escaping
    if body:
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")

        escaped_body = _escape_and_quote(body, warnings, "Request body")
        command += f" -d {escaped_body}"

    if not verify:
        command += " --insecure"

    command += f" {quote(url)}"

    return CurlCommand(command=command, warnings=warnings)


def _filter_headers(headers: dict[str, Any], known_generated_headers: dict[str, Any]) -> None:
    for key in list(headers):
        if key not in known_generated_headers and key in get_excluded_headers():
            del headers[key]


@lru_cache
def get_excluded_headers() -> CaseInsensitiveDict:
    from requests.structures import CaseInsensitiveDict
    from requests.utils import default_headers

    # These headers are added automatically by Schemathesis or `requests`.
    # Do not show them in code samples to make them more readable

    return CaseInsensitiveDict(
        {
            "Content-Length": None,
            "Transfer-Encoding": None,
            SCHEMATHESIS_TEST_CASE_HEADER: None,
            **default_headers(),
        }
    )
