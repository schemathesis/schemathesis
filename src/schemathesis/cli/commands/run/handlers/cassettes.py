from __future__ import annotations

import datetime
import json
import sys
import threading
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from queue import Queue
from typing import IO, Callable, Iterator
from urllib.parse import parse_qsl, urlparse

import harfile

from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.config import ProjectConfig, ReportFormat, SchemathesisConfig
from schemathesis.core.output.sanitization import sanitize_url, sanitize_value
from schemathesis.core.transforms import deepclone
from schemathesis.core.transport import Response
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine import Status, events
from schemathesis.engine.recorder import CheckNode, Request, ScenarioRecorder
from schemathesis.generation.meta import CoveragePhaseData

# Wait until the worker terminates
WRITER_WORKER_JOIN_TIMEOUT = 1


@dataclass
class CassetteWriter(EventHandler):
    """Write network interactions to a cassette."""

    format: ReportFormat
    path: Path
    config: ProjectConfig
    queue: Queue
    worker: threading.Thread

    __slots__ = ("format", "path", "config", "queue", "worker")

    def __init__(
        self,
        format: ReportFormat,
        path: Path,
        config: ProjectConfig,
        queue: Queue | None = None,
    ) -> None:
        self.format = format
        self.path = path
        self.config = config
        self.queue = queue or Queue()

        kwargs = {
            "path": self.path,
            "config": self.config,
            "queue": self.queue,
        }
        writer: Callable
        if self.format == ReportFormat.HAR:
            writer = har_writer
        else:
            writer = vcr_writer

        self.worker = threading.Thread(
            name="SchemathesisCassetteWriter",
            target=writer,
            kwargs=kwargs,
        )
        self.worker.start()

    def start(self, ctx: ExecutionContext) -> None:
        self.queue.put(Initialize(seed=ctx.config.seed))

    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, events.ScenarioFinished):
            self.queue.put(Process(recorder=event.recorder))

    def shutdown(self, ctx: ExecutionContext) -> None:
        self.queue.put(Finalize())
        self._stop_worker()

    def _stop_worker(self) -> None:
        self.worker.join(WRITER_WORKER_JOIN_TIMEOUT)


@dataclass
class Initialize:
    """Start up, the first message to make preparations before proceeding the input data."""

    seed: int | None

    __slots__ = ("seed",)


@dataclass
class Process:
    """A new chunk of data should be processed."""

    recorder: ScenarioRecorder

    __slots__ = ("recorder",)


@dataclass
class Finalize:
    """The work is done and there will be no more messages to process."""

    __slots__ = ()


def get_command_representation() -> str:
    """Get how Schemathesis was run."""
    # It is supposed to be executed from Schemathesis CLI, not via Click's `command.invoke`
    if not sys.argv[0].endswith(("schemathesis", "st")):
        return "<unknown entrypoint>"
    args = " ".join(sys.argv[1:])
    return f"st {args}"


def vcr_writer(path: Path, config: ProjectConfig, queue: Queue) -> None:
    """Write YAML to a file in an incremental manner.

    This implementation doesn't use `pyyaml` package and composes YAML manually as string due to the following reasons:
      - It is much faster. The string-based approach gives only ~2.5% time overhead when `yaml.CDumper` has ~11.2%;
      - Implementation complexity. We have a quite simple format where almost all values are strings, and it is much
        simpler to implement it with string composition rather than with adjusting `yaml.Serializer` to emit explicit
        types. Another point is that with `pyyaml` we need to emit events and handle some low-level details like
        providing tags, anchors to have incremental writing, with primitive types it is much simpler.
    """
    current_id = 1

    def format_header_values(values: list[str]) -> str:
        return "\n".join(f"      - {json.dumps(v)}" for v in values)

    if config.output.sanitization.enabled:

        def format_headers(headers: dict[str, list[str]]) -> str:
            headers = deepclone(headers)
            sanitize_value(headers, config=config.output.sanitization)
            return "\n".join(f'      "{name}":\n{format_header_values(values)}' for name, values in headers.items())

    else:

        def format_headers(headers: dict[str, list[str]]) -> str:
            return "\n".join(f'      "{name}":\n{format_header_values(values)}' for name, values in headers.items())

    def format_check_message(message: str | None) -> str:
        return "~" if message is None else f"{message!r}"

    def format_checks(checks: list[CheckNode]) -> str:
        if not checks:
            return "\n  checks: []"
        items = "\n".join(
            f"    - name: '{check.name}'\n      status: '{check.status.name.upper()}'\n      message: {format_check_message(check.failure_info.failure.title if check.failure_info else None)}"
            for check in checks
        )
        return f"""
  checks:
{items}"""

    if config.reports.preserve_bytes:

        def format_request_body(output: IO, request: Request) -> None:
            if request.encoded_body is not None:
                output.write(
                    f"""
    body:
      encoding: 'utf-8'
      base64_string: '{request.encoded_body}'"""
                )

        def format_response_body(output: IO, response: Response) -> None:
            if response.encoded_body is not None:
                output.write(
                    f"""    body:
      encoding: '{response.encoding}'
      base64_string: '{response.encoded_body}'"""
                )

    else:

        def format_request_body(output: IO, request: Request) -> None:
            if request.body is not None:
                string = request.body.decode("utf8", "replace")
                output.write(
                    """
    body:
      encoding: 'utf-8'
      string: """
                )
                write_double_quoted(output, string)

        def format_response_body(output: IO, response: Response) -> None:
            if response.content is not None:
                encoding = response.encoding or "utf8"
                string = response.content.decode(encoding, "replace")
                output.write(
                    f"""    body:
      encoding: '{encoding}'
      string: """
                )
                write_double_quoted(output, string)

    with open(path, "w", encoding="utf-8") as stream:
        while True:
            item = queue.get()
            if isinstance(item, Initialize):
                stream.write(
                    f"""command: '{get_command_representation()}'
recorded_with: 'Schemathesis {SCHEMATHESIS_VERSION}'
seed: {item.seed}
http_interactions:"""
                )
            elif isinstance(item, Process):
                for case_id, interaction in item.recorder.interactions.items():
                    case = item.recorder.cases[case_id]
                    if interaction.response is not None:
                        if case_id in item.recorder.checks:
                            checks = item.recorder.checks[case_id]
                            status = Status.SUCCESS
                            for check in checks:
                                if check.status == Status.FAILURE:
                                    status = check.status
                                    break
                        else:
                            # NOTE: Checks recording could be skipped if Schemathesis start skipping just
                            # discovered failures in order to get past them and potentially discover more failures
                            checks = []
                            status = Status.SKIP
                    else:
                        checks = []
                        status = Status.ERROR
                    # Body payloads are handled via separate `stream.write` calls to avoid some allocations
                    stream.write(
                        f"""\n- id: '{case_id}'
  status: '{status.name}'"""
                    )
                    meta = case.value.meta
                    if meta is not None:
                        # Start metadata block
                        stream.write(f"""
  generation:
    time: {meta.generation.time}
    mode: {meta.generation.mode.value}
  components:""")

                        # Write components
                        for kind, info in meta.components.items():
                            stream.write(f"""
    {kind.value}:
      mode: '{info.mode.value}'""")
                        # Write phase info
                        stream.write("\n  phase:")
                        stream.write(f"\n    name: '{meta.phase.name.value}'")
                        stream.write("\n    data: ")

                        # Write phase-specific data
                        if isinstance(meta.phase.data, CoveragePhaseData):
                            stream.write("""
      description: """)
                            write_double_quoted(stream, meta.phase.data.description)
                            stream.write("""
      location: """)
                            write_double_quoted(stream, meta.phase.data.location)
                            stream.write("""
      parameter: """)
                            if meta.phase.data.parameter is not None:
                                write_double_quoted(stream, meta.phase.data.parameter)
                            else:
                                stream.write("null")
                            stream.write("""
      parameter_location: """)
                            if meta.phase.data.parameter_location is not None:
                                write_double_quoted(stream, meta.phase.data.parameter_location)
                            else:
                                stream.write("null")
                        else:
                            # Empty objects for these phases
                            stream.write("{}")
                    else:
                        stream.write("null")

                    if config.output.sanitization.enabled:
                        uri = sanitize_url(interaction.request.uri, config=config.output.sanitization)
                    else:
                        uri = interaction.request.uri
                    recorded_at = datetime.datetime.fromtimestamp(
                        interaction.timestamp, datetime.timezone.utc
                    ).isoformat()
                    stream.write(
                        f"""
  recorded_at: '{recorded_at}'{format_checks(checks)}
  request:
    uri: '{uri}'
    method: '{interaction.request.method}'
    headers:
{format_headers(interaction.request.headers)}"""
                    )
                    format_request_body(stream, interaction.request)
                    if interaction.response is not None:
                        stream.write(
                            f"""
  response:
    status:
      code: '{interaction.response.status_code}'
      message: {json.dumps(interaction.response.message)}
    elapsed: '{interaction.response.elapsed}'
    headers:
{format_headers(interaction.response.headers)}
"""
                        )
                        format_response_body(stream, interaction.response)
                        stream.write(
                            f"""
    http_version: '{interaction.response.http_version}'"""
                        )
                    else:
                        stream.write("""
  response: null""")
                    current_id += 1
            else:
                break


def write_double_quoted(stream: IO, text: str | None) -> None:
    """Writes a valid YAML string enclosed in double quotes."""
    from yaml.emitter import Emitter

    if text is None:
        stream.write("null")
        return

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
                    data = f"\\x{ord(ch):02X}"
                elif ch <= "\uffff":
                    data = f"\\u{ord(ch):04X}"
                else:
                    data = f"\\U{ord(ch):08X}"
                stream.write(data)
                start = end + 1
        end += 1
    stream.write('"')


def har_writer(path: Path, config: SchemathesisConfig, queue: Queue) -> None:
    with harfile.open(path) as har:
        while True:
            item = queue.get()
            if isinstance(item, Process):
                for interaction in item.recorder.interactions.values():
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
                            headers = deepclone(interaction.response.headers)
                            sanitize_value(headers, config=config.output.sanitization)
                        else:
                            headers = interaction.response.headers
                        response = harfile.Response(
                            status=interaction.response.status_code,
                            httpVersion=http_version,
                            statusText=interaction.response.message,
                            headers=[harfile.Record(name=name, value=values[0]) for name, values in headers.items()],
                            cookies=_extract_cookies(headers.get("Set-Cookie", [])),
                            content=content,
                            headersSize=_headers_size(headers),
                            bodySize=interaction.response.body_size or 0,
                            redirectURL=headers.get("Location", [""])[0],
                        )
                        time = round(interaction.response.elapsed * 1000, 2)
                    else:
                        response = HARFILE_NO_RESPONSE
                        time = 0
                        http_version = ""

                    if config.output.sanitization.enabled:
                        headers = deepclone(interaction.request.headers)
                        sanitize_value(headers, config=config.output.sanitization)
                    else:
                        headers = interaction.request.headers
                    started_datetime = datetime.datetime.fromtimestamp(
                        interaction.timestamp, datetime.timezone.utc
                    ).isoformat()
                    har.add_entry(
                        startedDateTime=started_datetime,
                        time=time,
                        request=harfile.Request(
                            method=interaction.request.method.upper(),
                            url=uri,
                            httpVersion=http_version,
                            headers=[harfile.Record(name=name, value=values[0]) for name, values in headers.items()],
                            queryString=[
                                harfile.Record(name=name, value=value)
                                for name, value in parse_qsl(query_params, keep_blank_values=True)
                            ],
                            cookies=_extract_cookies(headers.get("Cookie", [])),
                            headersSize=_headers_size(headers),
                            bodySize=interaction.request.body_size or 0,
                            postData=post_data,
                        ),
                        response=response,
                        timings=harfile.Timings(send=0, wait=0, receive=time, blocked=0, dns=0, connect=0, ssl=0),
                    )
            elif isinstance(item, Finalize):
                break


HARFILE_NO_RESPONSE = harfile.Response(
    status=0,
    httpVersion="",
    statusText="",
    headers=[],
    cookies=[],
    content=harfile.Content(),
)


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
