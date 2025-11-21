from __future__ import annotations

import base64
import json
import string
from collections.abc import Mapping
from itertools import product
from typing import TYPE_CHECKING, Any

from schemathesis.core import NOT_SET
from schemathesis.core.version import SCHEMATHESIS_VERSION

if TYPE_CHECKING:
    import httpx
    import requests
    from werkzeug.test import TestResponse

    from schemathesis.generation.overrides import Override

USER_AGENT = f"schemathesis/{SCHEMATHESIS_VERSION}"
DEFAULT_RESPONSE_TIMEOUT = 10


def prepare_urlencoded(data: Any) -> Any:
    if isinstance(data, list):
        output = []
        for item in data:
            if isinstance(item, dict):
                for key, value in item.items():
                    output.append((key, value))
            else:
                output.append((item, "arbitrary-value"))
        return output
    return data


class Response:
    """HTTP response wrapper that normalizes different transport implementations.

    Provides a consistent interface for accessing response data whether the request
    was made via HTTP, ASGI, or WSGI transports.
    """

    status_code: int
    """HTTP status code (e.g., 200, 404, 500)."""
    headers: dict[str, list[str]]
    """Response headers with lowercase keys and list values."""
    content: bytes
    """Raw response body as bytes."""
    request: requests.PreparedRequest
    """The request that generated this response."""
    elapsed: float
    """Response time in seconds."""
    verify: bool
    """Whether TLS verification was enabled for the request."""
    message: str
    """HTTP status message (e.g., "OK", "Not Found")."""
    http_version: str
    """HTTP protocol version ("1.0" or "1.1")."""
    encoding: str | None
    """Character encoding for text content, if detected."""
    _override: Override | None

    __slots__ = (
        "status_code",
        "headers",
        "content",
        "request",
        "elapsed",
        "verify",
        "_deserialized",
        "message",
        "http_version",
        "encoding",
        "_encoded_body",
        "_override",
    )

    def __init__(
        self,
        status_code: int,
        headers: Mapping[str, list[str]],
        content: bytes,
        request: requests.PreparedRequest,
        elapsed: float,
        verify: bool,
        message: str = "",
        http_version: str = "1.1",
        encoding: str | None = None,
        _override: Override | None = None,
    ):
        self.status_code = status_code
        self.headers = {key.lower(): value for key, value in headers.items()}
        assert all(isinstance(v, list) for v in headers.values())
        self.content = content
        self.request = request
        self.elapsed = elapsed
        self.verify = verify
        self._deserialized = NOT_SET
        self._encoded_body: str | None = None
        self.message = message
        self.http_version = http_version
        self.encoding = encoding
        self._override = _override

    @classmethod
    def from_any(cls, response: Response | httpx.Response | requests.Response | TestResponse) -> Response:
        import httpx
        import requests
        from werkzeug.test import TestResponse

        if isinstance(response, requests.Response):
            return Response.from_requests(response, verify=True)
        elif isinstance(response, httpx.Response):
            return Response.from_httpx(response, verify=True)
        elif isinstance(response, TestResponse):
            return Response.from_wsgi(response)
        return response

    @classmethod
    def from_requests(cls, response: requests.Response, verify: bool, _override: Override | None = None) -> Response:
        raw = response.raw
        raw_headers = raw.headers if raw is not None else {}
        headers = {name: response.raw.headers.getlist(name) for name in raw_headers.keys()}
        # Similar to http.client:319 (HTTP version detection in stdlib's `http` package)
        version = raw.version if raw is not None else 10
        http_version = "1.0" if version == 10 else "1.1"
        return Response(
            status_code=response.status_code,
            headers=headers,
            content=response.content,
            request=response.request,
            elapsed=response.elapsed.total_seconds(),
            message=response.reason,
            encoding=response.encoding,
            http_version=http_version,
            verify=verify,
            _override=_override,
        )

    @classmethod
    def from_httpx(cls, response: httpx.Response, verify: bool) -> Response:
        import requests

        request = requests.Request(
            method=response.request.method,
            url=str(response.request.url),
            headers=dict(response.request.headers),
            params=dict(response.request.url.params),
            data=response.request.content,
        ).prepare()
        return Response(
            status_code=response.status_code,
            headers={key: [value] for key, value in response.headers.items()},
            content=response.content,
            request=request,
            elapsed=response.elapsed.total_seconds(),
            message=response.reason_phrase,
            encoding=response.encoding,
            http_version=response.http_version,
            verify=verify,
        )

    @classmethod
    def from_wsgi(cls, response: TestResponse) -> Response:
        import http.client

        import requests

        reason = http.client.responses.get(response.status_code, "Unknown")
        data = response.get_data()
        if response.response == []:
            # Werkzeug <3.0 had `charset` attr, newer versions always have UTF-8
            encoding = response.mimetype_params.get("charset", getattr(response, "charset", "utf-8"))
        else:
            encoding = None
        request = requests.Request(
            method=response.request.method,
            url=str(response.request.url),
            headers=dict(response.request.headers),
            params=dict(response.request.args),
            # Request body is not available
            data=b"",
        ).prepare()
        return Response(
            status_code=response.status_code,
            headers={name: response.headers.getlist(name) for name in response.headers.keys()},
            content=data,
            request=request,
            # Elapsed time is not available
            elapsed=0.0,
            message=reason,
            encoding=encoding,
            http_version="1.1",
            verify=False,
        )

    @property
    def text(self) -> str:
        """Decode response content as text using the detected or default encoding."""
        return self.content.decode(self.encoding if self.encoding else "utf-8")

    def json(self) -> Any:
        """Parse response content as JSON.

        Returns:
            Parsed JSON data (dict, list, or primitive types)

        Raises:
            json.JSONDecodeError: If content is not valid JSON

        """
        if self._deserialized is NOT_SET:
            self._deserialized = json.loads(self.text)
        return self._deserialized

    @property
    def body_size(self) -> int | None:
        """Size of response body in bytes, or None if no content."""
        return len(self.content) if self.content else None

    @property
    def encoded_body(self) -> str | None:
        """Base64-encoded response body for binary-safe serialization."""
        if self._encoded_body is None and self.content:
            self._encoded_body = base64.b64encode(self.content).decode()
        return self._encoded_body


def expand_status_code(status_code: str | int) -> list[int]:
    """Expand OpenAPI status code patterns like '2XX' or 'default' into concrete codes.

    Args:
        status_code: Status code pattern ('200', '2XX', 'default', etc.)

    Returns:
        List of concrete status codes matching the pattern

    """
    chars = [list(string.digits) if digit == "X" else [digit] for digit in str(status_code).upper()]
    return [int("".join(expanded)) for expanded in product(*chars)]


def status_code_matches(pattern: str, response_code: int) -> bool:
    """Check if a response status code matches an OpenAPI status code pattern.

    Args:
        pattern: OpenAPI status code pattern ('200', '2XX', 'default', etc.)
        response_code: Actual HTTP status code from response

    Returns:
        True if the response code matches the pattern

    """
    return pattern == str(response_code) or pattern == "default" or response_code in expand_status_code(pattern)
