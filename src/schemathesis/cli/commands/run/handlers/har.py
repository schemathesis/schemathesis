from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import RecordedScenarioHandler, write_until_finished
from schemathesis.reporting.har import HarWriter

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext


class HarHandler(RecordedScenarioHandler):
    """CLI event handler that writes network interactions to a HAR JSON file."""

    __slots__ = ()

    def start(self, ctx: BaseExecutionContext) -> None:
        writer = HarWriter(output=self.output, config=self.config, preserve_bytes=self.preserve_bytes)
        self._start_writer(
            "SchemathesisHarWriter",
            write_until_finished,
            writer=writer,
            open=partial(writer.open, seed=ctx.config.seed),
            write=writer.write,
        )
