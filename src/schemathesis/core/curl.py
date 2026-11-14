from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from shlex import quote
from typing import TYPE_CHECKING, Any

from schemathesis.core import SCHEMATHESIS_TEST_CASE_HEADER
from schemathesis.core.shell import escape_for_shell, has_non_printable

if TYPE_CHECKING:
    from requests.models import CaseInsensitiveDict


@dataclass(frozen=True, slots=True)
class CurlCommand:
    command: str
    """The curl command string."""

    warnings: list[str]
    """Warnings about non-printable characters or shell compatibility."""


def _escape_and_quote(value: str, warnings: list[str], ctx: str) -> str:
    if has_non_printable(value):
        escape_result = escape_for_shell(value)
        if escape_result.original_bytes is not None:
            # Unknown-shell path is the only one that surfaces raw bytes.
            warnings.append(f"{ctx} contains non-printable characters. Actual value: {escape_result.original_bytes!r}")
        elif escape_result.needs_warning:
            warnings.append(f"{ctx} was truncated for shell display.")
        return escape_result.escaped_value
    return quote(value)


def generate(
    *,
    method: str,
    url: str,
    body: Any,
    verify: bool,
    headers: dict[str, Any],
    known_generated_headers: dict[str, Any] | None,
) -> CurlCommand:
    _filter_headers(headers, known_generated_headers or {})
    warnings: list[str] = []
    command = f"curl -X {method}"

    for key, value in headers.items():
        # curl drops an empty header unless it ends with `;`
        if not value:
            header = f"{key};"
        else:
            header = f"{key}: {value}"

        escaped_header = _escape_and_quote(header, warnings, f"Header '{key}'")
        command += f" -H {escaped_header}"

    if body is not None:
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        elif not isinstance(body, str):
            body = str(body)

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

    # Schemathesis or `requests` add these headers; hiding them keeps code samples readable

    return CaseInsensitiveDict(
        {
            "Content-Length": None,
            "Transfer-Encoding": None,
            SCHEMATHESIS_TEST_CASE_HEADER: None,
            **default_headers(),
        }
    )
