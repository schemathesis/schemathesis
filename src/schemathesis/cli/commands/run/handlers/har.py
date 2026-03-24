from __future__ import annotations

import threading
from dataclasses import dataclass
from queue import Queue

from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.cli.commands.run.handlers.base import WRITER_WORKER_JOIN_TIMEOUT, EventHandler, TextOutput
from schemathesis.config import ProjectConfig
from schemathesis.engine import events
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.reporting.har import HarWriter


@dataclass
class HarHandler(EventHandler):
    """CLI event handler that writes network interactions to a HAR JSON file."""

    output: TextOutput
    config: ProjectConfig
    queue: Queue[_Initialize | _Process | _Finalize]
    worker: threading.Thread

    __slots__ = ("output", "config", "queue", "worker")

    def __init__(
        self,
        output: TextOutput,
        config: ProjectConfig,
        queue: Queue[_Initialize | _Process | _Finalize] | None = None,
    ) -> None:
        self.output = output
        self.config = config
        self.queue = queue or Queue()
        self.worker = threading.Thread(
            name="SchemathesisHarWriter",
            target=_run,
            kwargs={"output": self.output, "config": self.config, "queue": self.queue},
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
    config: ProjectConfig,
    queue: Queue[_Initialize | _Process | _Finalize],
) -> None:
    writer = HarWriter(output=output, config=config)
    while True:
        item = queue.get()
        if isinstance(item, _Initialize):
            writer.open(seed=item.seed)
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
