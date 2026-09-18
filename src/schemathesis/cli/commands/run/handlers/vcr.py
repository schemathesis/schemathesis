from __future__ import annotations

from dataclasses import dataclass
from queue import Queue
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import EventHandler, TextOutput, WriterWorker
from schemathesis.config import OutputConfig
from schemathesis.engine import events
from schemathesis.engine.recorder import RecordedScenario
from schemathesis.reporting._command import get_command_representation
from schemathesis.reporting.vcr import VcrWriter

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext


@dataclass(slots=True)
class VcrHandler(EventHandler):
    """CLI event handler that writes network interactions to a VCR YAML cassette."""

    output: TextOutput
    config: OutputConfig
    preserve_bytes: bool
    queue: Queue[_Initialize | _Process | _Finalize]
    worker: WriterWorker | None
    command: str

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
        self.command = get_command_representation(config.sanitization if config.sanitization.enabled else None)
        self.queue = queue or Queue()
        self.worker = None

    def start(self, ctx: BaseExecutionContext) -> None:
        # Cassette writing needs `yaml`; importing it on the writer thread instead deadlocks and loses events.
        import yaml.emitter  # noqa: F401

        self.worker = WriterWorker(
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
        self.queue.put(_Initialize(seed=ctx.config.seed))

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, (events.ScenarioFinished, events.FuzzScenarioFinished)):
            self.queue.put(_Process(recorder=event.recorder))

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        self.queue.put(_Finalize())
        if self.worker is not None:
            self.worker.join()


def _run(
    output: TextOutput,
    config: OutputConfig,
    preserve_bytes: bool,
    queue: Queue[_Initialize | _Process | _Finalize],
    command: str,
) -> None:
    with VcrWriter(output=output, config=config, preserve_bytes=preserve_bytes) as writer:
        while True:
            item = queue.get()
            if isinstance(item, _Initialize):
                writer.open(seed=item.seed, command=command)
            elif isinstance(item, _Process):
                writer.write(item.recorder)
            else:  # _Finalize
                break


@dataclass(slots=True)
class _Initialize:
    seed: int | None


@dataclass(slots=True)
class _Process:
    recorder: RecordedScenario


@dataclass(slots=True)
class _Finalize:
    pass
