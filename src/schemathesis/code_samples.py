from __future__ import annotations

from enum import Enum
from functools import lru_cache
from shlex import quote
from typing import TYPE_CHECKING

from .constants import SCHEMATHESIS_TEST_CASE_HEADER
from .types import Headers

if TYPE_CHECKING:
    from requests.structures import CaseInsensitiveDict


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


class CodeSampleStyle(str, Enum):
    """Controls the style of code samples for failure reproduction."""

    python = "python"
    curl = "curl"

    @property
    def verbose_name(self) -> str:
        return {
            self.curl: "cURL command",
            self.python: "Python code",
        }[self]

    @classmethod
    def default(cls) -> CodeSampleStyle:
        return cls.curl

    @classmethod
    def from_str(cls, value: str) -> CodeSampleStyle:
        try:
            return cls[value]
        except KeyError:
            available_styles = ", ".join(cls)
            raise ValueError(
                f"Invalid value for code sample style: {value}. Available styles: {available_styles}"
            ) from None

    def generate(
        self,
        *,
        method: str,
        url: str,
        body: str | bytes | None,
        headers: Headers | None,
        verify: bool,
        extra_headers: Headers | None = None,
    ) -> str:
        """Generate a code snippet for making HTTP requests."""
        handlers = {
            self.curl: _generate_curl,
            self.python: _generate_requests,
        }
        return handlers[self](
            method=method, url=url, body=body, headers=_filter_headers(headers, extra_headers), verify=verify
        )


def _filter_headers(headers: Headers | None, extra: Headers | None = None) -> Headers:
    headers = headers.copy() if headers else {}
    if extra is not None:
        for key, value in extra.items():
            if key not in get_excluded_headers():
                headers[key] = value
    return headers


def _generate_curl(
    *,
    method: str,
    url: str,
    body: str | bytes | None,
    headers: Headers,
    verify: bool,
) -> str:
    """Create a cURL command to reproduce an HTTP request."""
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


def _generate_requests(
    *,
    method: str,
    url: str,
    body: str | bytes | None,
    headers: Headers,
    verify: bool,
) -> str:
    """Create a Python code to reproduce an HTTP request."""
    url = _escape_single_quotes(url)
    command = f"requests.{method.lower()}('{url}'"
    if body:
        command += f", data={repr(body)}"
    if headers:
        command += f", headers={repr(headers)}"
    if not verify:
        command += ", verify=False"
    command += ")"
    return command


def _escape_single_quotes(url: str) -> str:
    """Escape single quotes in a string, so it is usable as in generated Python code.

    The usual ``str.replace`` is not suitable as it may convert already escaped quotes to not-escaped.
    """
    result = []
    escape = False
    for char in url:
        if escape:
            result.append(char)
            escape = False
        elif char == "\\":
            result.append(char)
            escape = True
        elif char == "'":
            result.append("\\'")
        else:
            result.append(char)
    return "".join(result)
