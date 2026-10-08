from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.commands.run.handlers.output import UNMATCHED_FILTER_TIP
from schemathesis.cli.events import LoadingFinished, LoadingStarted
from schemathesis.cli.output import LOADER_ERROR_SUGGESTIONS
from schemathesis.cli.summary import running_time
from schemathesis.core.errors import LoaderError, LoaderErrorKind
from schemathesis.core.output.sanitization import sanitize_url
from schemathesis.engine import StopReason, events
from schemathesis.engine.errors import EngineErrorInfo
from schemathesis.reporting.html.model import ErrorEntry, NothingTested, ReportData, ReportMeta

if TYPE_CHECKING:
    from pathlib import Path

    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.cli.summary import SummaryData
    from schemathesis.config import SanitizationConfig
    from schemathesis.engine.run import PhaseName


class HtmlReportHandler(EventHandler["BaseExecutionContext"]):
    __slots__ = ("output_dir", "location", "base_url", "started_at", "finished", "fatal_error", "last_phase")

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.location: str | None = None
        self.base_url: str | None = None
        self.started_at: float | None = None
        self.finished: events.EngineFinished | None = None
        self.fatal_error: ErrorEntry | None = None
        self.last_phase: PhaseName | None = None

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        # A failed load never reaches `LoadingFinished`, so the location is recorded as soon as it is known.
        if isinstance(event, LoadingStarted):
            self.location = event.location
        elif isinstance(event, LoadingFinished):
            self.location = event.location
            self.base_url = event.base_url
        elif isinstance(event, events.EngineStarted):
            self.started_at = event.timestamp
        elif isinstance(event, events.PhaseStarted):
            self.last_phase = event.phase.name
        elif isinstance(event, events.FatalError):
            self.fatal_error = _fatal_entry(event.exception, wait_for_schema=ctx.config.wait_for_schema)
        elif isinstance(event, events.EngineFinished):
            self.finished = event

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        from schemathesis.reporting._command import get_command_representation
        from schemathesis.reporting.html import write_report

        sanitization = ctx.config.output.sanitization
        sanitization_config = sanitization if sanitization.enabled else None
        summary = ctx.summary()
        data = ReportData(
            meta=ReportMeta(
                generated_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                location=_sanitized(self.location, sanitization_config),
                base_url=_sanitized(self.base_url, sanitization_config),
                command=get_command_representation(sanitization_config),
                seed=ctx.config.seed,
            ),
            summary=summary,
            fatal_error=self.fatal_error,
            nothing_tested=_nothing_tested(ctx.nothing_tested_reason, summary),
            last_phase=self.last_phase,
            running_time=running_time(self.started_at, self.finished),
            stop_reason=self.finished.stop_reason if self.finished is not None else StopReason.INTERRUPTED,
            started=self.started_at is not None,
            complete=self.finished is not None,
            exit_code=ctx.exit_code,
        )
        write_report(data, self.output_dir)


def _fatal_entry(exception: Exception, *, wait_for_schema: float | int | None) -> ErrorEntry:
    if isinstance(exception, LoaderError):
        tip = LOADER_ERROR_SUGGESTIONS.get(exception.kind)
        # The run already waited for the schema, so suggesting a wait would repeat what failed.
        if exception.kind == LoaderErrorKind.CONNECTION_OTHER and wait_for_schema is not None:
            tip = None
        return ErrorEntry(
            title="Failed to load specification", message="\n".join([exception.message, *exception.extras]), tip=tip
        )
    info = EngineErrorInfo(error=exception)
    return ErrorEntry(title=info.title, message=info.message, tip=None)


def _nothing_tested(reason: str | None, summary: SummaryData) -> NothingTested | None:
    if reason is None:
        return None
    operations = summary.operations
    if operations is not None and operations.total and not operations.selected:
        return NothingTested(
            title=f"0 of {operations.total} operation{'' if operations.total == 1 else 's'} matched the filters",
            filters=sorted(summary.warnings.unmatched_filter),
            tip=UNMATCHED_FILTER_TIP,
        )
    return NothingTested(title=reason, filters=[], tip=None)


def _sanitized(url: str | None, sanitization_config: SanitizationConfig | None) -> str | None:
    # The schema location may be a file path; only URLs can carry credentials.
    if url is None or sanitization_config is None or not url.startswith(("http://", "https://")):
        return url
    return sanitize_url(url, config=sanitization_config)
