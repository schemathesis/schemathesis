from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import EventHandler

if TYPE_CHECKING:
    from pathlib import Path

    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.engine import events


class HtmlReportHandler(EventHandler["BaseExecutionContext"]):
    __slots__ = ("output_dir",)

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        pass

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        from schemathesis.reporting.html import write_report

        generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        write_report(self.output_dir, generated_at=generated_at)
