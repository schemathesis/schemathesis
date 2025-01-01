from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import requests


def serialize_payload(payload: bytes) -> str:
    return base64.b64encode(payload).decode()


@dataclass(repr=False)
class Request:
    """Request data extracted from `Case`."""

    method: str
    uri: str
    body: bytes | None
    body_size: int | None
    headers: dict[str, list[str]]

    __slots__ = ("method", "uri", "body", "body_size", "headers", "_encoded_body_cache")

    def __init__(
        self,
        method: str,
        uri: str,
        body: bytes | None,
        body_size: int | None,
        headers: dict[str, list[str]],
    ):
        self.method = method
        self.uri = uri
        self.body = body
        self.body_size = body_size
        self.headers = headers
        self._encoded_body_cache: str | None = None

    @classmethod
    def from_prepared_request(cls, prepared: requests.PreparedRequest) -> Request:
        """A prepared request version is already stored in `requests.Response`."""
        body = prepared.body

        if isinstance(body, str):
            # can be a string for `application/x-www-form-urlencoded`
            body = body.encode("utf-8")

        # these values have `str` type at this point
        uri = cast(str, prepared.url)
        method = cast(str, prepared.method)
        return cls(
            uri=uri,
            method=method,
            headers={key: [value] for (key, value) in prepared.headers.items()},
            body=body,
            body_size=len(body) if body is not None else None,
        )

    @property
    def encoded_body(self) -> str | None:
        if self.body is not None:
            if self._encoded_body_cache is None:
                self._encoded_body_cache = serialize_payload(self.body)
            return self._encoded_body_cache
        return None

    def asdict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "uri": self.uri,
            "body": self.encoded_body,
            "body_size": self.body_size,
            "headers": self.headers,
        }
