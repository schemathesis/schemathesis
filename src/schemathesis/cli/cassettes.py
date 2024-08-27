from __future__ import annotations

import base64
import enum
import json
import re
import sys
import threading
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from queue import Queue
from typing import IO, TYPE_CHECKING, Any, Callable, Generator, Iterator, cast
from urllib.parse import parse_qsl, urlparse

import harfile

from ..constants import SCHEMATHESIS_VERSION
from ..runner import events
from ..types import RequestCert
from .handlers import EventHandler

if TYPE_CHECKING:
    import click
    import requests

    from ..models import Request, Response
    from ..runner.serialization import SerializedCheck, SerializedInteraction
    from .context import ExecutionContext

# Wait until the worker terminates
WRITER_WORKER_JOIN_TIMEOUT = 1


class CassetteFormat(str, enum.Enum):
    """Type of the cassette."""

    VCR = "vcr"
    HAR = "har"

    @classmethod
    def from_str(cls, value: str) -> CassetteFormat:
        try:
            return cls[value.upper()]
        except KeyError:
            available_formats = ", ".join(cls)
            raise ValueError(
                f"Invalid value for cassette format: {value}. Available formats: {available_formats}"
            ) from None


@dataclass
class CassetteWriter(EventHandler):
    """Write interactions in a YAML cassette.

    A low-level interface is used to write data to YAML file during the test run and reduce the delay at
    the end of the test run.
    """

    file_handle: click.utils.LazyFile
    format: CassetteFormat
    preserve_exact_body_bytes: bool
    queue: Queue = field(default_factory=Queue)
    worker: threading.Thread = field(init=False)

    def __post_init__(self) -> None:
        kwargs = {
            "file_handle": self.file_handle,
            "queue": self.queue,
            "preserve_exact_body_bytes": self.preserve_exact_body_bytes,
        }
        writer: Callable
        if self.format == CassetteFormat.HAR:
            writer = har_writer
        else:
            writer = vcr_writer
        self.worker = threading.Thread(name="SchemathesisCassetteWriter", target=writer, kwargs=kwargs)
        self.worker.start()

    def handle_event(self, context: ExecutionContext, event: events.ExecutionEvent) -> None:
        if isinstance(event, events.Initialized):
            # In the beginning we write metadata and start `http_interactions` list
            self.queue.put(Initialize())
        elif isinstance(event, events.AfterExecution):
            # Seed is always present at this point, the original Optional[int] type is there because `TestResult`
            # instance is created before `seed` is generated on the hypothesis side
            seed = cast(int, event.result.seed)
            self.queue.put(
                Process(
                    seed=seed,
                    correlation_id=event.correlation_id,
                    thread_id=event.thread_id,
                    interactions=event.result.interactions,
                )
            )
        elif isinstance(event, events.AfterStatefulExecution):
            seed = cast(int, event.result.seed)
            self.queue.put(
                Process(
                    seed=seed,
                    # Correlation ID is not used in stateful testing
                    correlation_id="",
                    thread_id=event.thread_id,
                    interactions=event.result.interactions,
                )
            )
        elif isinstance(event, events.Finished):
            self.shutdown()

    def shutdown(self) -> None:
        self.queue.put(Finalize())
        self._stop_worker()

    def _stop_worker(self) -> None:
        self.worker.join(WRITER_WORKER_JOIN_TIMEOUT)


@dataclass
class Initialize:
    """Start up, the first message to make preparations before proceeding the input data."""


@dataclass
class Process:
    """A new chunk of data should be processed."""

    seed: int
    correlation_id: str
    thread_id: int
    interactions: list[SerializedInteraction]


@dataclass
class Finalize:
    """The work is done and there will be no more messages to process."""


def get_command_representation() -> str:
    """Get how Schemathesis was run."""
    # It is supposed to be executed from Schemathesis CLI, not via Click's `command.invoke`
    if not sys.argv[0].endswith(("schemathesis", "st")):
        return "<unknown entrypoint>"
    args = " ".join(sys.argv[1:])
    return f"st {args}"


def vcr_writer(file_handle: click.utils.LazyFile, preserve_exact_body_bytes: bool, queue: Queue) -> None:
    """Write YAML to a file in an incremental manner.

    This implementation doesn't use `pyyaml` package and composes YAML manually as string due to the following reasons:
      - It is much faster. The string-based approach gives only ~2.5% time overhead when `yaml.CDumper` has ~11.2%;
      - Implementation complexity. We have a quite simple format where almost all values are strings, and it is much
        simpler to implement it with string composition rather than with adjusting `yaml.Serializer` to emit explicit
        types. Another point is that with `pyyaml` we need to emit events and handle some low-level details like
        providing tags, anchors to have incremental writing, with primitive types it is much simpler.
    """
    current_id = 1
    stream = file_handle.open()

    def format_header_values(values: list[str]) -> str:
        return "\n".join(f"      - {json.dumps(v)}" for v in values)

    def format_headers(headers: dict[str, list[str]]) -> str:
        return "\n".join(f'      "{name}":\n{format_header_values(values)}' for name, values in headers.items())

    def format_check_message(message: str | None) -> str:
        return "~" if message is None else f"{repr(message)}"

    def format_checks(checks: list[SerializedCheck]) -> str:
        return "\n".join(
            f"    - name: '{check.name}'\n      status: '{check.value.name.upper()}'\n      message: {format_check_message(check.message)}"
            for check in checks
        )

    if preserve_exact_body_bytes:

        def format_request_body(output: IO, request: Request) -> None:
            if request.body is not None:
                output.write(
                    f"""
    body:
      encoding: 'utf-8'
      base64_string: '{request.body}'"""
                )

        def format_response_body(output: IO, response: Response) -> None:
            if response.body is not None:
                output.write(
                    f"""    body:
      encoding: '{response.encoding}'
      base64_string: '{response.body}'"""
                )

    else:

        def format_request_body(output: IO, request: Request) -> None:
            if request.body is not None:
                string = _safe_decode(request.body, "utf8")
                output.write(
                    """
    body:
      encoding: 'utf-8'
      string: """
                )
                write_double_quoted(output, string)

        def format_response_body(output: IO, response: Response) -> None:
            if response.body is not None:
                encoding = response.encoding or "utf8"
                string = _safe_decode(response.body, encoding)
                output.write(
                    f"""    body:
      encoding: '{encoding}'
      string: """
                )
                write_double_quoted(output, string)

    while True:
        item = queue.get()
        if isinstance(item, Initialize):
            stream.write(
                f"""command: '{get_command_representation()}'
recorded_with: 'Schemathesis {SCHEMATHESIS_VERSION}'
http_interactions:"""
            )
        elif isinstance(item, Process):
            for interaction in item.interactions:
                status = interaction.status.name.upper()
                # Body payloads are handled via separate `stream.write` calls to avoid some allocations
                phase = f"'{interaction.phase.value}'" if interaction.phase is not None else "null"
                stream.write(
                    f"""\n- id: '{current_id}'
  status: '{status}'
  seed: '{item.seed}'
  thread_id: {item.thread_id}
  correlation_id: '{item.correlation_id}'
  data_generation_method: '{interaction.data_generation_method.value}'
  phase: {phase}
  elapsed: '{interaction.response.elapsed}'
  recorded_at: '{interaction.recorded_at}'
  checks:
{format_checks(interaction.checks)}
  request:
    uri: '{interaction.request.uri}'
    method: '{interaction.request.method}'
    headers:
{format_headers(interaction.request.headers)}"""
                )
                format_request_body(stream, interaction.request)
                stream.write(
                    f"""
  response:
    status:
      code: '{interaction.response.status_code}'
      message: {json.dumps(interaction.response.message)}
    headers:
{format_headers(interaction.response.headers)}
"""
                )
                format_response_body(stream, interaction.response)
                stream.write(
                    f"""
    http_version: '{interaction.response.http_version}'"""
                )
                current_id += 1
        else:
            break
    file_handle.close()


def _safe_decode(value: str, encoding: str) -> str:
    """Decode base64-encoded body bytes as a string."""
    return base64.b64decode(value).decode(encoding, "replace")


def write_double_quoted(stream: IO, text: str) -> None:
    """Writes a valid YAML string enclosed in double quotes."""
    from yaml.emitter import Emitter

    # Adapted from `yaml.Emitter.write_double_quoted`:
    #   - Doesn't split the string, therefore doesn't track the current column
    #   - Doesn't encode the input
    #   - Allows Unicode unconditionally
    stream.write('"')
    start = end = 0
    length = len(text)
    while end <= length:
        ch = None
        if end < length:
            ch = text[end]
        if (
            ch is None
            or ch in '"\\\x85\u2028\u2029\ufeff'
            or not ("\x20" <= ch <= "\x7e" or ("\xa0" <= ch <= "\ud7ff" or "\ue000" <= ch <= "\ufffd"))
        ):
            if start < end:
                stream.write(text[start:end])
                start = end
            if ch is not None:
                # Escape character
                if ch in Emitter.ESCAPE_REPLACEMENTS:
                    data = "\\" + Emitter.ESCAPE_REPLACEMENTS[ch]
                elif ch <= "\xff":
                    data = "\\x%02X" % ord(ch)
                elif ch <= "\uffff":
                    data = "\\u%04X" % ord(ch)
                else:
                    data = "\\U%08X" % ord(ch)
                stream.write(data)
                start = end + 1
        end += 1
    stream.write('"')


def har_writer(file_handle: click.utils.LazyFile, preserve_exact_body_bytes: bool, queue: Queue) -> None:
    if preserve_exact_body_bytes:

        def get_body(body: str) -> str:
            return body
    else:

        def get_body(body: str) -> str:
            return base64.b64decode(body).decode("utf-8", errors="replace")

    with harfile.open(file_handle) as har:
        while True:
            item = queue.get()
            if isinstance(item, Process):
                for interaction in item.interactions:
                    time = round(interaction.response.elapsed * 1000, 2)
                    content_type = interaction.response.headers.get("Content-Type", [""])[0]
                    content = harfile.Content(
                        size=interaction.response.body_size or 0,
                        mimeType=content_type,
                        text=get_body(interaction.response.body) if interaction.response.body is not None else None,
                        encoding="base64"
                        if interaction.response.body is not None and preserve_exact_body_bytes
                        else None,
                    )
                    http_version = f"HTTP/{interaction.response.http_version}"
                    query_params = urlparse(interaction.request.uri).query
                    if interaction.request.body is not None:
                        post_data = harfile.PostData(
                            mimeType=content_type,
                            text=get_body(interaction.request.body),
                        )
                    else:
                        post_data = None
                    har.add_entry(
                        startedDateTime=interaction.recorded_at,
                        time=time,
                        request=harfile.Request(
                            method=interaction.request.method.upper(),
                            url=interaction.request.uri,
                            httpVersion=http_version,
                            headers=[
                                harfile.Record(name=name, value=values[0])
                                for name, values in interaction.request.headers.items()
                            ],
                            queryString=[
                                harfile.Record(name=name, value=value)
                                for name, value in parse_qsl(query_params, keep_blank_values=True)
                            ],
                            cookies=_extract_cookies(interaction.request.headers.get("Cookie", [])),
                            headersSize=_headers_size(interaction.request.headers),
                            bodySize=interaction.request.body_size or 0,
                            postData=post_data,
                        ),
                        response=harfile.Response(
                            status=interaction.response.status_code,
                            httpVersion=http_version,
                            statusText=interaction.response.message,
                            headers=[
                                harfile.Record(name=name, value=values[0])
                                for name, values in interaction.response.headers.items()
                            ],
                            cookies=_extract_cookies(interaction.response.headers.get("Set-Cookie", [])),
                            content=content,
                            headersSize=_headers_size(interaction.response.headers),
                            bodySize=interaction.response.body_size or 0,
                            redirectURL=interaction.response.headers.get("Location", [""])[0],
                        ),
                        timings=harfile.Timings(send=0, wait=0, receive=time, blocked=0, dns=0, connect=0, ssl=0),
                    )
            elif isinstance(item, Finalize):
                break


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


@dataclass
class Replayed:
    interaction: dict[str, Any]
    response: requests.Response


def replay(
    cassette: dict[str, Any],
    id_: str | None = None,
    status: str | None = None,
    uri: str | None = None,
    method: str | None = None,
    request_tls_verify: bool = True,
    request_cert: RequestCert | None = None,
    request_proxy: str | None = None,
) -> Generator[Replayed, None, None]:
    """Replay saved interactions."""
    import requests

    session = requests.Session()
    session.verify = request_tls_verify
    session.cert = request_cert
    kwargs = {}
    if request_proxy is not None:
        kwargs["proxies"] = {"all": request_proxy}
    for interaction in filter_cassette(cassette["http_interactions"], id_, status, uri, method):
        request = get_prepared_request(interaction["request"])
        response = session.send(request, **kwargs)  # type: ignore
        yield Replayed(interaction, response)


def filter_cassette(
    interactions: list[dict[str, Any]],
    id_: str | None = None,
    status: str | None = None,
    uri: str | None = None,
    method: str | None = None,
) -> Iterator[dict[str, Any]]:
    filters = []

    def id_filter(item: dict[str, Any]) -> bool:
        return item["id"] == id_

    def status_filter(item: dict[str, Any]) -> bool:
        status_ = cast(str, status)
        return item["status"].upper() == status_.upper()

    def uri_filter(item: dict[str, Any]) -> bool:
        uri_ = cast(str, uri)
        return bool(re.search(uri_, item["request"]["uri"]))

    def method_filter(item: dict[str, Any]) -> bool:
        method_ = cast(str, method)
        return bool(re.search(method_, item["request"]["method"]))

    if id_ is not None:
        filters.append(id_filter)

    if status is not None:
        filters.append(status_filter)

    if uri is not None:
        filters.append(uri_filter)

    if method is not None:
        filters.append(method_filter)

    def is_match(interaction: dict[str, Any]) -> bool:
        return all(filter_(interaction) for filter_ in filters)

    return filter(is_match, interactions)


def get_prepared_request(data: dict[str, Any]) -> requests.PreparedRequest:
    """Create a `requests.PreparedRequest` from a serialized one."""
    import requests
    from requests.cookies import RequestsCookieJar
    from requests.structures import CaseInsensitiveDict

    prepared = requests.PreparedRequest()
    prepared.method = data["method"]
    prepared.url = data["uri"]
    prepared._cookies = RequestsCookieJar()  # type: ignore
    if "body" in data:
        body = data["body"]
        if "base64_string" in body:
            content = body["base64_string"]
            if content:
                prepared.body = base64.b64decode(content)
        else:
            content = body["string"]
            if content:
                prepared.body = content.encode("utf8")
    # There is always 1 value in a request
    headers = [(key, value[0]) for key, value in data["headers"].items()]
    prepared.headers = CaseInsensitiveDict(headers)
    return prepared
