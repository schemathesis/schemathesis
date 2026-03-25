from __future__ import annotations

import threading
from dataclasses import dataclass
from queue import Queue

from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.cli.commands.run.handlers.base import WRITER_WORKER_JOIN_TIMEOUT, EventHandler, TextOutput
from schemathesis.config import OutputConfig
from schemathesis.engine import events
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.reporting._command import get_command_representation
from schemathesis.reporting.vcr import VcrWriter


@dataclass
class VcrHandler(EventHandler):
    """CLI event handler that writes network interactions to a VCR YAML cassette."""

    output: TextOutput
    config: OutputConfig
    preserve_bytes: bool
    queue: Queue[_Initialize | _Process | _Finalize]
    worker: threading.Thread
    command: str

    __slots__ = ("output", "config", "preserve_bytes", "queue", "worker", "command")

    def __init__(
        self,
        output: TextOutput,
        config: OutputConfig,
        preserve_bytes: bool = False,
        queue: Queue[_Initialize | _Process | _Finalize] | None = None,
    ) -> None:
        self.output = output
        self.config = config
        self.preserve_bytes = preserve_bytes
        self.command = get_command_representation()
        self.queue = queue or Queue()
        self.worker = threading.Thread(
            name="SchemathesisVcrWriter",
            target=_run,
            kwargs={
                "output": self.output,
                "config": self.config,
                "preserve_bytes": self.preserve_bytes,
                "queue": self.queue,
                "command": self.command,
            },
        )
        self.worker.start()

    def start(self, ctx: ExecutionContext) -> None:
        self.queue.put(_Initialize(seed=ctx.config.seed))

    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, events.ScenarioFinished):
            self.queue.put(_Process(recorder=event.recorder))

    def shutdown(self, ctx: ExecutionContext) -> None:
        self.queue.put(_Finalize())
        self.worker.join(WRITER_WORKER_JOIN_TIMEOUT)


def _run(
    output: TextOutput,
    config: OutputConfig,
    preserve_bytes: bool,
    queue: Queue[_Initialize | _Process | _Finalize],
    command: str,
) -> None:
    writer = VcrWriter(output=output, config=config, preserve_bytes=preserve_bytes)
    while True:
        item = queue.get()
        if isinstance(item, _Initialize):
            writer.open(seed=item.seed, command=command)
        elif isinstance(item, _Process):
            writer.write(item.recorder)
        else:  # _Finalize
            writer.close()
            break


@dataclass
class _Initialize:
    seed: int | None
    __slots__ = ("seed",)


@dataclass
class _Process:
    recorder: ScenarioRecorder
    __slots__ = ("recorder",)


@dataclass
class _Finalize:
    __slots__ = ()
