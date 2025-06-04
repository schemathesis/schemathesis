from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any, Mapping

from schemathesis.core.version import SCHEMATHESIS_VERSION

if TYPE_CHECKING:
    import requests

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
        "_json",
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
        self._json = None
        self._encoded_body: str | None = None
        self.message = message
        self.http_version = http_version
        self.encoding = encoding
        self._override = _override

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
        if self._json is None:
            self._json = json.loads(self.text)
        return self._json

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
