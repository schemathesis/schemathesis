import sys
import threading
from contextlib import contextmanager
from queue import Queue
from typing import Any, Dict, Generator, List, Optional, cast

import attr
import click
import yaml
from yaml.serializer import Serializer

from .. import constants
from ..models import Interaction
from ..runner import events
from .context import ExecutionContext
from .handlers import EventHandler

try:
    from yaml import CDumper as Dumper
except ImportError:
    # pylint: disable=unused-import
    from yaml import Loader, Dumper  # type: ignore


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
            self.queue.put(Finalize())
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


class StringSerializer(Serializer):
    """Emit scalar values as strings.

    It is required to avoid possible issues with default YAML parsing.
    For example "Norway problem", where `- no` will be parsed to `[False]`, but we have strings everywhere
    therefore we need `- 'no'` and ['no'].
    """

    def serialize_node(self, node: yaml.Node, parent: Optional[yaml.Node], index: int) -> None:
        # NOTE. This implementation is taken from the parent Serializer and adjusted for `ScalarNode` case and
        # for `MappingNode`.
        alias = self.anchors[node]
        self.serialized_nodes[node] = True
        self.descend_resolver(parent, index)  # type: ignore
        if isinstance(node, yaml.ScalarNode):
            implicit = False, True
            self.emit(yaml.ScalarEvent(alias, node.tag, implicit, node.value, style=node.style))  # type: ignore
        elif isinstance(node, yaml.SequenceNode):
            implicit = node.tag == self.resolve(yaml.SequenceNode, node.value, True)  # type: ignore
            self.emit(yaml.SequenceStartEvent(alias, node.tag, implicit, flow_style=node.flow_style))  # type: ignore
            index = 0
            for item in node.value:
                self.serialize_node(item, node, index)
                index += 1
            self.emit(yaml.SequenceEndEvent())  # type: ignore
        elif isinstance(node, yaml.MappingNode):
            implicit = node.tag == self.resolve(yaml.MappingNode, node.value, True)  # type: ignore
            self.emit(yaml.MappingStartEvent(alias, node.tag, implicit, flow_style=node.flow_style))  # type: ignore
            for key, value in node.value:
                self.emit(yaml.ScalarEvent(alias, key.tag, (True, True), key.value, style=key.style))  # type: ignore
                self.serialize_node(value, node, key)
            self.emit(yaml.MappingEndEvent())  # type: ignore
        self.ascend_resolver()  # type: ignore


class StringDumper(Dumper, StringSerializer):
    pass


def get_command_representation() -> str:
    """Get how Schemathesis was run."""
    # It is supposed to be executed from Schemathesis CLI, not via Click's `command.invoke`
    if not sys.argv[0].endswith("schemathesis"):
        return "<unknown entrypoint>"
    args = " ".join(sys.argv[1:])
    return f"schemathesis {args}"


def worker(file_handle: click.utils.LazyFile, queue: Queue) -> None:
    """Write YAML to a file in an incremental manner."""
    current_id = 0
    stream = file_handle.open()
    dumper = StringDumper(stream, sort_keys=False)  # type: ignore
    StringSerializer.__init__(dumper)  # type: ignore
    dumper.open()  # type: ignore

    # Helpers

    def emit(*yaml_events: yaml.Event) -> None:
        for event in yaml_events:
            dumper.emit(event)  # type: ignore

    @contextmanager
    def mapping() -> Generator[None, None, None]:
        emit(yaml.MappingStartEvent(anchor=None, tag=None, implicit=True))
        yield
        emit(yaml.MappingEndEvent())

    def key(name: str) -> yaml.ScalarEvent:
        """Default style for mapping keys is without quotes."""
        return yaml.ScalarEvent(anchor=None, tag=None, implicit=(True, True), value=name)

    def value(_value: str) -> yaml.ScalarEvent:
        """Default style for mapping values is with quotes."""
        return yaml.ScalarEvent(anchor=None, tag=None, implicit=(False, True), value=_value)

    def serialize_mapping(name: str, data: Dict[str, Any]) -> None:
        emit(key(name))
        node = dumper.represent_data(data)  # type: ignore
        # C-extension is not introspectable
        dumper.anchor_node(node)  # type: ignore
        dumper.serialize_node(node, None, 0)  # type: ignore

    while True:
        item = queue.get()
        if isinstance(item, Initialize):
            emit(yaml.DocumentStartEvent(), yaml.MappingStartEvent(anchor=None, tag=None, implicit=True))
            emit(
                key("command"),
                value(get_command_representation()),
                key("recorded_with"),
                value(f"Schemathesis {constants.__version__}"),
                key("http_interactions"),
                yaml.SequenceStartEvent(anchor=None, tag=None, implicit=True),
            )
        elif isinstance(item, Process):
            for interaction in item.interactions:
                with mapping():
                    emit(
                        key("id"),
                        value(str(current_id)),
                        key("status"),
                        value(item.status),
                        key("seed"),
                        value(str(item.seed)),
                        key("elapsed"),
                        value(str(interaction.response.elapsed)),
                        key("recorded_at"),
                        value(interaction.recorded_at),
                    )
                    serialize_mapping("request", interaction.request.asdict())
                    serialize_mapping("response", interaction.response.asdict())
                current_id += 1
        else:
            emit(yaml.SequenceEndEvent(), yaml.MappingEndEvent(), yaml.DocumentEndEvent())
            # C-extension is not introspectable
            dumper.close()  # type: ignore
            dumper.dispose()  # type: ignore
            break
