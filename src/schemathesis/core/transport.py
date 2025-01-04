from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any, Mapping

from schemathesis.core.version import SCHEMATHESIS_VERSION

if TYPE_CHECKING:
    import requests

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
    """Unified response for both testing and reporting purposes."""

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

    @classmethod
    def from_requests(cls, response: requests.Response, verify: bool) -> Response:
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
        )

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding if self.encoding else "utf-8")

    def json(self) -> Any:
        if self._json is None:
            self._json = json.loads(self.text)
        return self._json

    @property
    def body_size(self) -> int | None:
        return len(self.content) if self.content else None

    @property
    def encoded_body(self) -> str | None:
        if self._encoded_body is None and self.content:
            self._encoded_body = base64.b64encode(self.content).decode()
        return self._encoded_body
