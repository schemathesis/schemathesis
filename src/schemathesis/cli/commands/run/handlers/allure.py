from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.events import LoadingFinished
from schemathesis.engine import events
from schemathesis.reporting.recorders import scenario_records

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.config import OutputConfig
    from schemathesis.reporting.allure import AllureWriter


class AllureHandler(EventHandler):
    __slots__ = ("writer",)

    def __init__(self, output_dir: Path, config: OutputConfig) -> None:
        from schemathesis.reporting.allure import AllureWriter

        self.writer: AllureWriter = AllureWriter(output_dir=output_dir, config=config)

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        for record in scenario_records(ctx.statistic, event):
            self.writer.record_scenario(
                label=record.label,
                elapsed_sec=record.elapsed_sec,
                status=record.status,
                failures=record.failures,
                skip_reason=record.skip_reason,
                tags=_operation_tags(ctx, record.label) if record.tagged else None,
            )
        if isinstance(event, LoadingFinished):
            self.writer.api_title = event.title
        elif isinstance(event, events.NonFatalError):
            self.writer.record_error(
                label=event.label, message=event.info.format(base_url_is_set=ctx.config.base_url is not None)
            )

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        self.writer.unskip(ctx.statistic.tested_operations)
        self.writer.close()


def _operation_tags(ctx: BaseExecutionContext, label: str) -> list[str] | None:
    operation = ctx.find_operation(label)
    return operation.tags if operation is not None else None
