from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import RecordedScenarioHandler, TextOutput, write_until_finished
from schemathesis.config import OutputConfig
from schemathesis.reporting._command import get_command_representation
from schemathesis.reporting.vcr import VcrWriter

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext


class VcrHandler(RecordedScenarioHandler):
    """CLI event handler that writes network interactions to a VCR YAML cassette."""

    __slots__ = ("command",)

    def __init__(self, output: TextOutput, config: OutputConfig, preserve_bytes: bool = False) -> None:
        super().__init__(output, config, preserve_bytes)
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
