from __future__ import annotations

import threading
from dataclasses import dataclass
from queue import Queue
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import WRITER_WORKER_JOIN_TIMEOUT, EventHandler, TextOutput
from schemathesis.config import ProjectConfig, SanitizationConfig
from schemathesis.engine import events
from schemathesis.reporting._command import get_command_representation
from schemathesis.reporting.ndjson import NdjsonWriter

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext


class NdjsonHandler(EventHandler):
    """CLI event handler that writes engine events to NDJSON format via a background thread."""

    __slots__ = ("output", "config", "queue", "worker")

    def __init__(
        self,
        output: TextOutput,
        config: ProjectConfig,
        queue: Queue[_Initialize | _Process | _Finalize] | None = None,
    ) -> None:
        self.output = output
        self.config = config
        self.queue: Queue[_Initialize | _Process | _Finalize] = queue or Queue()
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

    def start(self, ctx: BaseExecutionContext) -> None:
        self.queue.put(_Initialize(seed=ctx.config.seed, command=get_command_representation()))

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        self.queue.put(_Process(payload=event))

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        self.queue.put(_Finalize())
        self.worker.join(WRITER_WORKER_JOIN_TIMEOUT)


def _run(
    output: TextOutput,
    queue: Queue[_Initialize | _Process | _Finalize],
    sanitization: SanitizationConfig | None,
) -> None:
    writer = NdjsonWriter(output=output, sanitization=sanitization)
    while True:
        item = queue.get()
        if isinstance(item, _Initialize):
            writer.open(seed=item.seed, command=item.command)
        elif isinstance(item, _Process):
            writer.write_event(item.payload)
        else:  # _Finalize
            writer.close()
            break


@dataclass(slots=True)
class _Initialize:
    command: str
    seed: int | None


@dataclass(slots=True)
class _Process:
    payload: events.EngineEvent


@dataclass(slots=True)
class _Finalize:
    pass
