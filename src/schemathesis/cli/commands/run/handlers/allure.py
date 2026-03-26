from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.engine import Status, events
from schemathesis.engine.run import PhaseName

if TYPE_CHECKING:
    from schemathesis.cli.commands.run.context import ExecutionContext
    from schemathesis.config import OutputConfig
    from schemathesis.reporting.allure import AllureWriter


class AllureHandler(EventHandler):
    __slots__ = ("writer",)

    def __init__(self, output_dir: Path, config: OutputConfig) -> None:
        from schemathesis.reporting.allure import AllureWriter

        self.writer: AllureWriter = AllureWriter(output_dir=output_dir, config=config)

    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, events.ScenarioFinished):
            label = event.recorder.label
            failures = list(ctx.statistic.failures.get(label, {}).values()) if event.status == Status.FAILURE else []
            if event.phase != PhaseName.STATEFUL_TESTING and ctx.find_operation_by_label is not None:
                operation = ctx.find_operation_by_label(label)
            else:
                operation = None
            self.writer.record_scenario(
                label=label,
                elapsed_sec=event.elapsed_time,
                status=event.status,
                failures=failures,
                skip_reason=event.skip_reason,
                tags=operation.tags if operation is not None else None,
            )
        elif isinstance(event, events.NonFatalError):
            self.writer.record_error(label=event.label, message=event.info.format())

    def shutdown(self, ctx: ExecutionContext) -> None:
        self.writer.close()
