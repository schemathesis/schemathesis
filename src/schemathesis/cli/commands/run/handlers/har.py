from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import BackgroundWriterHandler, TextOutput, write_until_finished
from schemathesis.config import OutputConfig
from schemathesis.engine import events
from schemathesis.engine.recorder import RecordedScenario
from schemathesis.reporting.har import HarWriter

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext


class HarHandler(BackgroundWriterHandler[RecordedScenario]):
    """CLI event handler that writes network interactions to a HAR JSON file."""

    __slots__ = ("output", "config", "preserve_bytes")

    def __init__(
        self,
        output: TextOutput,
        config: OutputConfig,
        preserve_bytes: bool = False,
    ) -> None:
        super().__init__()
        self.output = output
        self.config = config
        self.preserve_bytes = preserve_bytes

    def start(self, ctx: BaseExecutionContext) -> None:
        writer = HarWriter(output=self.output, config=self.config, preserve_bytes=self.preserve_bytes)
        self._start_writer(
            "SchemathesisHarWriter",
            write_until_finished,
            writer=writer,
            open=partial(writer.open, seed=ctx.config.seed),
            write=writer.write,
        )

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, (events.ScenarioFinished, events.FuzzScenarioFinished)):
            self.queue.put(event.recorder)
