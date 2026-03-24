from __future__ import annotations

import datetime
from collections.abc import Iterator
from http.cookies import SimpleCookie
from io import StringIO
from pathlib import Path
from types import TracebackType
from typing import IO
from urllib.parse import parse_qsl, urlparse

import harfile

from schemathesis.config import ProjectConfig
from schemathesis.core.output.sanitization import sanitize_url, sanitize_value
from schemathesis.core.transforms import deepclone
from schemathesis.engine.recorder import ScenarioRecorder

TextOutput = IO[str] | StringIO | Path

HARFILE_NO_RESPONSE = harfile.Response(
    status=0,
    httpVersion="",
    statusText="",
    headers=[],
    cookies=[],
    content=harfile.Content(),
)


class HarWriter:
    """Write network interactions to a HAR JSON cassette file."""

    def __init__(self, output: TextOutput, config: ProjectConfig) -> None:
        self._output = output
        self._config = config
        self._har: harfile.HarFile | None = None

    def open(self, seed: int | None = None) -> None:
        """Open the HAR output file. `seed` is ignored — HAR has no header field for it."""
        self._ctx = harfile.open(self._output)
        self._har = self._ctx.__enter__()

    def write(self, recorder: ScenarioRecorder) -> None:
        """Write all interactions from a ScenarioRecorder to the HAR file."""
        har = self._har
        assert har is not None
        config = self._config

        for interaction in recorder.interactions.values():
            if config.output.sanitization.enabled:
                uri = sanitize_url(interaction.request.uri, config=config.output.sanitization)
            else:
                uri = interaction.request.uri
            query_params = urlparse(uri).query
            if interaction.request.body is not None:
                post_data = harfile.PostData(
                    mimeType=interaction.request.headers.get("Content-Type", [""])[0],
                    text=interaction.request.encoded_body
                    if config.reports.preserve_bytes
                    else interaction.request.body.decode("utf-8", "replace"),
                )
            else:
                post_data = None
            if interaction.response is not None:
                content_type = interaction.response.headers.get("Content-Type", [""])[0]
                content = harfile.Content(
                    size=interaction.response.body_size or 0,
                    mimeType=content_type,
                    text=interaction.response.encoded_body
                    if config.reports.preserve_bytes
                    else interaction.response.content.decode("utf-8", "replace")
                    if interaction.response.content is not None
                    else None,
                    encoding="base64"
                    if interaction.response.content is not None and config.reports.preserve_bytes
                    else None,
                )
                http_version = f"HTTP/{interaction.response.http_version}"
                if config.output.sanitization.enabled:
                    resp_headers = deepclone(interaction.response.headers)
                    sanitize_value(resp_headers, config=config.output.sanitization)
                else:
                    resp_headers = interaction.response.headers
                response = harfile.Response(
                    status=interaction.response.status_code,
                    httpVersion=http_version,
                    statusText=interaction.response.message,
                    headers=[harfile.Record(name=name, value=values[0]) for name, values in resp_headers.items()],
                    cookies=_extract_cookies(resp_headers.get("Set-Cookie", [])),
                    content=content,
                    headersSize=_headers_size(resp_headers),
                    bodySize=interaction.response.body_size or 0,
                    redirectURL=resp_headers.get("Location", [""])[0],
                )
                time = round(interaction.response.elapsed * 1000, 2)
            else:
                response = HARFILE_NO_RESPONSE
                time = 0
                http_version = ""

            if config.output.sanitization.enabled:
                req_headers = deepclone(interaction.request.headers)
                sanitize_value(req_headers, config=config.output.sanitization)
            else:
                req_headers = interaction.request.headers
            started_datetime = datetime.datetime.fromtimestamp(interaction.timestamp, datetime.timezone.utc).isoformat()
            har.add_entry(
                startedDateTime=started_datetime,
                time=time,
                request=harfile.Request(
                    method=interaction.request.method.upper(),
                    url=uri,
                    httpVersion=http_version,
                    headers=[harfile.Record(name=name, value=values[0]) for name, values in req_headers.items()],
                    queryString=[
                        harfile.Record(name=name, value=value)
                        for name, value in parse_qsl(query_params, keep_blank_values=True)
                    ],
                    cookies=_extract_cookies(req_headers.get("Cookie", [])),
                    headersSize=_headers_size(req_headers),
                    bodySize=interaction.request.body_size or 0,
                    postData=post_data,
                ),
                response=response,
                timings=harfile.Timings(send=0, wait=0, receive=time, blocked=0, dns=0, connect=0, ssl=0),
            )

    def close(self) -> None:
        """Close the HAR output file."""
        if self._har is not None:
            self._ctx.__exit__(None, None, None)
            self._har = None

    def __enter__(self) -> HarWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()


def _headers_size(headers: dict[str, list[str]]) -> int:
    size = 0
    for name, values in headers.items():
        # 4 is for ": " and "\r\n"
        size += len(name) + 4 + len(values[0])
    return size


def _extract_cookies(headers: list[str]) -> list[harfile.Cookie]:
    return [cookie for items in headers for item in items for cookie in _cookie_to_har(item)]


def _cookie_to_har(cookie: str) -> Iterator[harfile.Cookie]:
    parsed = SimpleCookie(cookie)
    for name, data in parsed.items():
        yield harfile.Cookie(
            name=name,
            value=data.value,
            path=data["path"] or None,
            domain=data["domain"] or None,
            expires=data["expires"] or None,
            httpOnly=data["httponly"] or None,
            secure=data["secure"] or None,
        )
