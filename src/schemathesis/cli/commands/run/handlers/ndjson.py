from __future__ import annotations

import threading
from dataclasses import dataclass
from queue import Queue

from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.cli.commands.run.handlers.base import EventHandler, TextOutput, get_command_representation
from schemathesis.config import ProjectConfig, SanitizationConfig
from schemathesis.engine import events
from schemathesis.reporting.ndjson import NdjsonWriter

_WRITER_WORKER_JOIN_TIMEOUT = 1


class NdjsonHandler(EventHandler):
    """CLI event handler that writes engine events to NDJSON format via a background thread."""

    __slots__ = ("output", "config", "queue", "worker")

    def __init__(
        self,
        output: TextOutput,
        config: ProjectConfig,
        queue: Queue | None = None,
    ) -> None:
        self.output = output
        self.config = config
        self.queue: Queue = queue or Queue()
        sanitization = config.output.sanitization if config.output.sanitization.enabled else None
        self.worker = threading.Thread(
            name="SchemathesisNdjsonWriter",
            target=_run,
            kwargs={
                "output": self.output,
                "queue": self.queue,
                "sanitization": sanitization,
            },
        )
        self.worker.start()

    def start(self, ctx: ExecutionContext) -> None:
        self.queue.put(_Initialize(seed=ctx.config.seed, command=get_command_representation()))

    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        self.queue.put(_Event(payload=event))

    def shutdown(self, ctx: ExecutionContext) -> None:
        self.queue.put(_Shutdown())
        self.worker.join(_WRITER_WORKER_JOIN_TIMEOUT)


@dataclass(slots=True)
class _Initialize:
    command: str
    seed: int | None


@dataclass(slots=True)
class _Event:
    payload: events.EngineEvent


@dataclass(slots=True)
class _Shutdown:
    pass


def _run(output: TextOutput, queue: Queue, sanitization: SanitizationConfig | None) -> None:
    writer = NdjsonWriter(output=output, sanitization=sanitization)
    while True:
        item = queue.get()
        if isinstance(item, _Initialize):
            writer.open(seed=item.seed, command=item.command)
        elif isinstance(item, _Event):
            writer.write_event(item.payload)
        else:  # _Shutdown
            writer.close()
            break
