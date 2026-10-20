from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import BackgroundWriterHandler, TextOutput, write_until_finished
from schemathesis.config import ProjectConfig
from schemathesis.engine import events
from schemathesis.reporting._command import get_command_representation
from schemathesis.reporting.ndjson import NdjsonWriter

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext


class NdjsonHandler(BackgroundWriterHandler[events.EngineEvent]):
    """CLI event handler that writes engine events to NDJSON format via a background thread."""

    __slots__ = ("output", "config")

    def __init__(
        self,
        output: TextOutput,
        config: ProjectConfig,
    ) -> None:
        super().__init__()
        self.output = output
        self.config = config

    def start(self, ctx: BaseExecutionContext) -> None:
        sanitization = self.config.output.sanitization
        writer = NdjsonWriter(output=self.output, sanitization=sanitization if sanitization.enabled else None)
        self._start_writer(
            "SchemathesisNdjsonWriter",
            write_until_finished,
            writer=writer,
            open=partial(
                writer.open,
                seed=ctx.config.seed,
                command=get_command_representation(sanitization),
            ),
            write=writer.write_event,
        )

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        self.queue.put(event)
