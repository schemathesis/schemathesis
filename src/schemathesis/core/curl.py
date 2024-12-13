from __future__ import annotations

from functools import lru_cache
from shlex import quote
from typing import TYPE_CHECKING, Any

from schemathesis.core import SCHEMATHESIS_TEST_CASE_HEADER

if TYPE_CHECKING:
    from requests.models import CaseInsensitiveDict


def generate(
    *,
    method: str,
    url: str,
    body: str | bytes | None,
    verify: bool,
    headers: dict[str, Any],
    known_generated_headers: dict[str, Any] | None,
) -> str:
    """Generate a code snippet for making HTTP requests."""
    _filter_headers(headers, known_generated_headers or {})
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
