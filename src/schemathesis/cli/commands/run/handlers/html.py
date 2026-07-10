from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.commands.run.warnings import WarningCollector
from schemathesis.cli.events import LoadingFinished
from schemathesis.core.compat import RefResolutionError
from schemathesis.engine import Status, events
from schemathesis.engine.errors import EngineErrorInfo
from schemathesis.engine.run import PhaseName
from schemathesis.reporting._command import get_command_representation
from schemathesis.reporting.recorders import (
    scenario_elapsed,
    scenario_failures,
    scenario_failures_by_case,
    scenario_has_failures,
    unique_labels,
)

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.config import ProjectConfig
    from schemathesis.engine.statistic import GroupedFailures
    from schemathesis.reporting.html import HtmlReportWriter


class HtmlReportHandler(EventHandler):
    __slots__ = ("writer", "collector", "_definitions")

    def __init__(self, output_dir: Path, config: ProjectConfig) -> None:
        from schemathesis.reporting.html import HtmlReportWriter

        self.writer: HtmlReportWriter = HtmlReportWriter(output_dir=output_dir, config=config.output)
        self.collector = WarningCollector(config=config)
        # Every scenario event for a label would otherwise re-fetch and re-serialize the same
        # operation definition; cache it per label since it cannot change within a run.
        self._definitions: dict[str, tuple[str | None, str | None]] = {}

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, LoadingFinished):
            # The loaded schema may match a `[[project]]` config section that differs from the
            # CLI-entry default; warning rules must come from the resolved config.
            self.collector.config = event.config
            self.writer.set_meta(
                location=event.location,
                base_url=event.base_url,
                command=get_command_representation(sanitization=event.config.output.sanitization),
                seed=event.config.seed,
            )
        elif isinstance(event, events.PhaseStarted):
            self.writer.record_phase_started(event.phase.name, at=event.timestamp)
        elif isinstance(event, events.PhaseFinished):
            self.writer.record_phase_finished(event.phase.name, at=event.timestamp)
        elif isinstance(event, events.ScenarioFinished):
            self.collector.on_scenario_finished(ctx, event)
            # Stateful scenarios carry no per-operation label; report them under the recorder's
            # aggregate label ("Stateful tests"), which is also how their failures are keyed.
            label = event.label if event.label is not None else event.recorder.label
            if event.status == Status.FAILURE:
                failures = scenario_failures_by_case(ctx.statistic, event.recorder)
            else:
                failures = []
            self._record(
                ctx,
                event,
                label=label,
                phase=event.phase,
                skip_reason=event.skip_reason,
                status=event.status,
                failures=failures,
                elapsed_sec=event.elapsed_time,
            )
        elif isinstance(event, events.FuzzScenarioFinished):
            if event.status in (Status.SUCCESS, Status.FAILURE):
                for label in unique_labels(event.recorder):
                    status = Status.FAILURE if scenario_has_failures(event.recorder, label) else Status.SUCCESS
                    failures = (
                        scenario_failures(ctx.statistic, event.recorder, label) if status == Status.FAILURE else []
                    )
                    self._record(
                        ctx,
                        event,
                        label=label,
                        phase=PhaseName.FUZZING,
                        skip_reason=None,
                        status=status,
                        failures=failures,
                        # A fuzz scenario spans multiple operations; `event.elapsed_time` covers all of
                        # them, so each label's own duration must come from just its own interactions.
                        elapsed_sec=scenario_elapsed(event.recorder, label),
                    )
        elif isinstance(event, events.SchemaAnalysisWarnings):
            self.collector.on_schema_warnings(ctx, event)
        elif isinstance(event, events.NonFatalError):
            self.writer.record_error(
                label=event.label,
                title=event.info.title or type(event.value).__name__,
                message=event.info.message or str(event.value),
                traceback=event.info.traceback if event.info.has_useful_traceback else None,
                phase=event.phase.value if event.phase is not None else None,
            )
        elif isinstance(event, events.FatalError):
            info = EngineErrorInfo(error=event.exception)
            self.writer.record_fatal_error(title=info.title, message=info.message)
        elif isinstance(event, events.EngineFinished):
            # v1: run-level `after_run` check failures (event.failures) are not yet surfaced in the report.
            self.writer.set_run_summary(
                running_time=event.running_time,
                stop_reason=event.stop_reason.value,
            )

    def _record(
        self,
        ctx: BaseExecutionContext,
        event: events.ScenarioFinished | events.FuzzScenarioFinished,
        *,
        label: str,
        phase: PhaseName,
        skip_reason: str | None,
        status: Status,
        failures: list[GroupedFailures],
        elapsed_sec: float,
    ) -> None:
        if label in self._definitions:
            summary, definition = self._definitions[label]
        else:
            summary = None
            definition = None
            if ctx.find_operation_by_label is not None:
                try:
                    operation = ctx.find_operation_by_label(label)
                except RefResolutionError:
                    operation = None
                if operation is not None:
                    raw = operation.definition.raw
                    # `raw` is a JSON-shaped mapping for OpenAPI operations; GraphQL fields have no `.get`.
                    if isinstance(raw, dict):
                        summary = raw.get("summary")
                        definition = json.dumps(raw, indent=2, default=str)
            self._definitions[label] = (summary, definition)
        self.writer.record_scenario(
            label=label,
            elapsed_sec=elapsed_sec,
            status=status,
            phase=phase,
            recorder=event.recorder,
            failures=failures,
            skip_reason=skip_reason,
            at=event.timestamp,
            summary=summary,
            definition=definition,
        )

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        self.writer.set_warnings(self.collector.data)
        self.writer.close(exit_code=ctx.exit_code)
