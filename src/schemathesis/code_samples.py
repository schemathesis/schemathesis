from enum import Enum
from shlex import quote
from typing import Any, Optional

from requests.structures import CaseInsensitiveDict
from requests.utils import default_headers

from .constants import SCHEMATHESIS_TEST_CASE_HEADER, DataGenerationMethod

DEFAULT_DATA_GENERATION_METHODS = (DataGenerationMethod.default(),)
# These headers are added automatically by Schemathesis or `requests`.
# Do not show them in code samples to make them more readable
EXCLUDED_HEADERS = CaseInsensitiveDict(
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

    @classmethod
    def default(cls) -> "CodeSampleStyle":
        return cls.curl

    @classmethod
    def from_str(cls, value: str) -> "CodeSampleStyle":
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
        body: Any,
        headers: CaseInsensitiveDict,
        verify: bool,
        include_headers: Optional[CaseInsensitiveDict] = None,
    ) -> str:
        """Generate a code snippet for making HTTP requests."""
        handlers = {
            self.curl: _generate_curl,
            self.python: _generate_requests,
        }
        return handlers[self](
            method=method, url=url, body=body, headers=_filter_headers(headers, include_headers), verify=verify
        )


def _filter_headers(
    headers: CaseInsensitiveDict, include_headers: Optional[CaseInsensitiveDict] = None
) -> CaseInsensitiveDict:
    include_headers = include_headers or CaseInsensitiveDict({})
    return CaseInsensitiveDict(
        {key: val for key, val in headers.items() if key not in EXCLUDED_HEADERS or key in include_headers}
    )


def _generate_curl(
    *,
    method: str,
    url: str,
    body: Any,
    headers: CaseInsensitiveDict,
    verify: bool,
) -> str:
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
    body: Any,
    headers: CaseInsensitiveDict,
    verify: bool,
) -> str:
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
