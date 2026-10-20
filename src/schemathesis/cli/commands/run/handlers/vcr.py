from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import BackgroundWriterHandler, TextOutput, write_until_finished
from schemathesis.config import OutputConfig
from schemathesis.engine import events
from schemathesis.engine.recorder import RecordedScenario
from schemathesis.reporting._command import get_command_representation
from schemathesis.reporting.vcr import VcrWriter

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext


class VcrHandler(BackgroundWriterHandler[RecordedScenario]):
    """CLI event handler that writes network interactions to a VCR YAML cassette."""

    __slots__ = ("output", "config", "preserve_bytes", "command")

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
        self.command = get_command_representation(config.sanitization)

    def start(self, ctx: BaseExecutionContext) -> None:
        # Cassette writing needs `yaml`; importing it on the writer thread instead deadlocks and loses events.
        import yaml.emitter  # noqa: F401

        writer = VcrWriter(output=self.output, config=self.config, preserve_bytes=self.preserve_bytes)
        self._start_writer(
            "SchemathesisVcrWriter",
            write_until_finished,
            writer=writer,
            open=partial(writer.open, seed=ctx.config.seed, command=self.command),
            write=writer.write,
        )

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, (events.ScenarioFinished, events.FuzzScenarioFinished)):
            self.queue.put(event.recorder)
