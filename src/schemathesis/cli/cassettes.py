import base64
import json
import re
import sys
import threading
from queue import Queue
from typing import IO, Any, Dict, Generator, Iterator, List, Optional, cast

import attr
import click
import requests
from requests.cookies import RequestsCookieJar
from requests.structures import CaseInsensitiveDict
from yaml.emitter import Emitter

from .. import constants
from ..models import Request, Response
from ..runner import events
from ..runner.serialization import SerializedCheck, SerializedInteraction
from ..types import RequestCert
from .context import ExecutionContext
from .handlers import EventHandler

# Wait until the worker terminates
WRITER_WORKER_JOIN_TIMEOUT = 1


@attr.s(slots=True)  # pragma: no mutate
class CassetteWriter(EventHandler):
    """Write interactions in a YAML cassette.

    A low-level interface is used to write data to YAML file during the test run and reduce the delay at
    the end of the test run.
    """

    file_handle: click.utils.LazyFile = attr.ib()  # pragma: no mutate
    preserve_exact_body_bytes: bool = attr.ib()  # pragma: no mutate
    queue: Queue = attr.ib(factory=Queue)  # pragma: no mutate
    worker: threading.Thread = attr.ib(init=False)  # pragma: no mutate

    def __attrs_post_init__(self) -> None:
        self.worker = threading.Thread(
            target=worker,
            kwargs={
                "file_handle": self.file_handle,
                "preserve_exact_body_bytes": self.preserve_exact_body_bytes,
                "queue": self.queue,
            },
        )
        self.worker.start()

    def handle_event(self, context: ExecutionContext, event: events.ExecutionEvent) -> None:
        if isinstance(event, events.Initialized):
            # In the beginning we write metadata and start `http_interactions` list
            self.queue.put(Initialize())
        if isinstance(event, events.AfterExecution):
            # Seed is always present at this point, the original Optional[int] type is there because `TestResult`
            # instance is created before `seed` is generated on the hypothesis side
            seed = cast(int, event.result.seed)
            self.queue.put(
                Process(
                    seed=seed,
                    interactions=event.result.interactions,
                )
            )
        if isinstance(event, events.Finished):
            self.shutdown()

    def shutdown(self) -> None:
        self.queue.put(Finalize())
        self._stop_worker()

    def _stop_worker(self) -> None:
        self.worker.join(WRITER_WORKER_JOIN_TIMEOUT)


@attr.s(slots=True)  # pragma: no mutate
class Initialize:
    """Start up, the first message to make preparations before proceeding the input data."""


@attr.s(slots=True)  # pragma: no mutate
class Process:
    """A new chunk of data should be processed."""

    seed: int = attr.ib()  # pragma: no mutate
    interactions: List[SerializedInteraction] = attr.ib()  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class Finalize:
    """The work is done and there will be no more messages to process."""


def get_command_representation() -> str:
    """Get how Schemathesis was run."""
    # It is supposed to be executed from Schemathesis CLI, not via Click's `command.invoke`
    if not sys.argv[0].endswith(("schemathesis", "st")):
        return "<unknown entrypoint>"
    args = " ".join(sys.argv[1:])
    return f"st {args}"


def worker(file_handle: click.utils.LazyFile, preserve_exact_body_bytes: bool, queue: Queue) -> None:
    """Write YAML to a file in an incremental manner.

    This implementation doesn't use `pyyaml` package and composes YAML manually as string due to the following reasons:
      - It is much faster. The string-based approach gives only ~2.5% time overhead when `yaml.CDumper` has ~11.2%;
      - Implementation complexity. We have a quite simple format where all values are strings, and it is much simpler to
        implement it with string composition rather than with adjusting `yaml.Serializer` to emit explicit types.
        Another point is that with `pyyaml` we need to emit events and handle some low-level details like providing
        tags, anchors to have incremental writing, with strings it is much simpler.
    """
    current_id = 1
    stream = file_handle.open()

    def format_header_values(values: List[str]) -> str:
        return "\n".join(f"      - {json.dumps(v)}" for v in values)

    def format_headers(headers: Dict[str, List[str]]) -> str:
        return "\n".join(f"      {name}:\n{format_header_values(values)}" for name, values in headers.items())

    def format_check_message(message: Optional[str]) -> str:
        return "~" if message is None else f"{repr(message)}"

    def format_checks(checks: List[SerializedCheck]) -> str:
        return "\n".join(
            f"    - name: '{check.name}'\n      status: '{check.value.name.upper()}'\n      message: {format_check_message(check.message)}"
            for check in checks
        )

    if preserve_exact_body_bytes:

        def format_request_body(output: IO, request: Request) -> None:
            if request.body is not None:
                output.write(
                    f"""    body:
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
                    """    body:
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
recorded_with: 'Schemathesis {constants.__version__}'
http_interactions:"""
            )
        elif isinstance(item, Process):
            for interaction in item.interactions:
                status = interaction.status.name.upper()
                # Body payloads are handled via separate `stream.write` calls to avoid some allocations
                stream.write(
                    f"""\n- id: '{current_id}'
  status: '{status}'
  seed: '{item.seed}'
  elapsed: '{interaction.response.elapsed}'
  recorded_at: '{interaction.recorded_at}'
  checks:
{format_checks(interaction.checks)}
  request:
    uri: '{interaction.request.uri}'
    method: '{interaction.request.method}'
    headers:
{format_headers(interaction.request.headers)}
"""
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
            or ch in '"\\\x85\u2028\u2029\uFEFF'
            or not ("\x20" <= ch <= "\x7E" or ("\xA0" <= ch <= "\uD7FF" or "\uE000" <= ch <= "\uFFFD"))
        ):
            if start < end:
                stream.write(text[start:end])
                start = end
            if ch is not None:
                # Escape character
                if ch in Emitter.ESCAPE_REPLACEMENTS:
                    data = "\\" + Emitter.ESCAPE_REPLACEMENTS[ch]
                elif ch <= "\xFF":
                    data = "\\x%02X" % ord(ch)
                elif ch <= "\uFFFF":
                    data = "\\u%04X" % ord(ch)
                else:
                    data = "\\U%08X" % ord(ch)
                stream.write(data)
                start = end + 1
        end += 1
    stream.write('"')


@attr.s(slots=True)  # pragma: no mutate
class Replayed:
    interaction: Dict[str, Any] = attr.ib()  # pragma: no mutate
    response: requests.Response = attr.ib()  # pragma: no mutate


def replay(
    cassette: Dict[str, Any],
    id_: Optional[str] = None,
    status: Optional[str] = None,
    uri: Optional[str] = None,
    method: Optional[str] = None,
    request_tls_verify: bool = True,
    request_cert: Optional[RequestCert] = None,
) -> Generator[Replayed, None, None]:
    """Replay saved interactions."""
    session = requests.Session()
    session.verify = request_tls_verify
    session.cert = request_cert
    for interaction in filter_cassette(cassette["http_interactions"], id_, status, uri, method):
        request = get_prepared_request(interaction["request"])
        response = session.send(request)  # type: ignore
        yield Replayed(interaction, response)


def filter_cassette(
    interactions: List[Dict[str, Any]],
    id_: Optional[str] = None,
    status: Optional[str] = None,
    uri: Optional[str] = None,
    method: Optional[str] = None,
) -> Iterator[Dict[str, Any]]:

    filters = []

    def id_filter(item: Dict[str, Any]) -> bool:
        return item["id"] == id_

    def status_filter(item: Dict[str, Any]) -> bool:
        status_ = cast(str, status)
        return item["status"].upper() == status_.upper()

    def uri_filter(item: Dict[str, Any]) -> bool:
        uri_ = cast(str, uri)
        return bool(re.search(uri_, item["request"]["uri"]))

    def method_filter(item: Dict[str, Any]) -> bool:
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

    def is_match(interaction: Dict[str, Any]) -> bool:
        return all(filter_(interaction) for filter_ in filters)

    return filter(is_match, interactions)


def get_prepared_request(data: Dict[str, Any]) -> requests.PreparedRequest:
    """Create a `requests.PreparedRequest` from a serialized one."""
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
