import sys
import threading
from queue import Queue
from typing import Dict, List, cast

import attr
import click

from .. import constants
from ..models import Interaction
from ..runner import events
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
    queue: Queue = attr.ib(factory=Queue)  # pragma: no mutate
    worker: threading.Thread = attr.ib(init=False)  # pragma: no mutate

    def __attrs_post_init__(self) -> None:
        self.worker = threading.Thread(target=worker, kwargs={"file_handle": self.file_handle, "queue": self.queue})
        self.worker.start()

    def handle_event(self, context: ExecutionContext, event: events.ExecutionEvent) -> None:
        if isinstance(event, events.Initialized):
            # In the beginning we write metadata and start `http_interactions` list
            self.queue.put(Initialize())
        if isinstance(event, events.AfterExecution):
            # Seed is always present at this point, the original Optional[int] type is there because `TestResult`
            # instance is created before `seed` is generated on the hypothesis side
            seed = cast(int, event.result.seed)
            self.queue.put(Process(status=event.status.name.upper(), seed=seed, interactions=event.result.interactions))
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

    status: str = attr.ib()  # pragma: no mutate
    seed: int = attr.ib()  # pragma: no mutate
    interactions: List[Interaction] = attr.ib()  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class Finalize:
    """The work is done and there will be no more messages to process."""


def get_command_representation() -> str:
    """Get how Schemathesis was run."""
    # It is supposed to be executed from Schemathesis CLI, not via Click's `command.invoke`
    if not sys.argv[0].endswith("schemathesis"):
        return "<unknown entrypoint>"
    args = " ".join(sys.argv[1:])
    return f"schemathesis {args}"


def worker(file_handle: click.utils.LazyFile, queue: Queue) -> None:
    """Write YAML to a file in an incremental manner.

    This implementation doesn't use `pyyaml` package and composes YAML manually as string due to the following reasons:
      - It is much faster. The string-based approach gives only ~2.5% time overhead when `yaml.CDumper` has ~11.2%;
      - Implementation complexity. We have a quite simple format where all values are strings and it is much simpler to
        implement it with string composition rather than with adjusting `yaml.Serializer` to emit explicit types.
        Another point is that with `pyyaml` we need to emit events and handle some low-level details like providing
        tags, anchors to have incremental writing, with strings it is much simpler.
    """
    current_id = 1
    stream = file_handle.open()

    def format_header_values(values: List[str]) -> str:
        return "\n".join(f"      - '{v}'" for v in values)

    def format_headers(headers: Dict[str, List[str]]) -> str:
        return "\n".join(f"      {name}:\n{format_header_values(values)}" for name, values in headers.items())

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
                stream.write(
                    f"""\n- id: '{current_id}'
  status: '{item.status}'
  seed: '{item.seed}'
  elapsed: '{interaction.response.elapsed}'
  recorded_at: '{interaction.recorded_at}'
  request:
    uri: '{interaction.request.uri}'
    method: '{interaction.request.method}'
    headers:
{format_headers(interaction.request.headers)}
    body:
      encoding: 'utf-8'
      base64_string: '{interaction.request.body}'
  response:
    status:
      code: '{interaction.response.status_code}'
      message: '{interaction.response.message}'
    headers:
{format_headers(interaction.response.headers)}
    body:
      encoding: '{interaction.response.encoding}'
      base64_string: '{interaction.response.body}'
    http_version: '{interaction.response.http_version}'"""
                )
                current_id += 1
        else:
            break
    file_handle.close()
