from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.events import LoadingFinished, LoadingStarted
from schemathesis.cli.summary import running_time
from schemathesis.core.errors import LoaderError
from schemathesis.core.output.sanitization import sanitize_url
from schemathesis.engine import StopReason, events
from schemathesis.engine.errors import EngineErrorInfo
from schemathesis.reporting.html.model import ErrorEntry, ReportData, ReportMeta

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.config import SanitizationConfig


class HtmlReportHandler(EventHandler["BaseExecutionContext"]):
    __slots__ = ("output_dir", "location", "base_url", "started_at", "finished", "fatal_error", "errors")

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.location: str | None = None
        self.base_url: str | None = None
        self.started_at: float | None = None
        self.finished: events.EngineFinished | None = None
        self.fatal_error: ErrorEntry | None = None
        # First-seen detail per title; the summary supplies the counts.
        self.errors: dict[str, ErrorEntry] = {}

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        # A failed load never reaches `LoadingFinished`, so the location is recorded as soon as it is known.
        if isinstance(event, LoadingStarted):
            self.location = event.location
        elif isinstance(event, LoadingFinished):
            self.location = event.location
            self.base_url = event.base_url
        elif isinstance(event, events.EngineStarted):
            self.started_at = event.timestamp
        elif isinstance(event, events.NonFatalError):
            info = event.info
            entry = self.errors.setdefault(
                info.title,
                ErrorEntry(
                    operations=[],
                    title=info.title,
                    message=info.message,
                    traceback=info.traceback if info.has_useful_traceback else None,
                    phase=event.phase.value if event.phase is not None else None,
                ),
            )
            if event.label not in entry.operations:
                entry.operations.append(event.label)
        elif isinstance(event, events.FatalError):
            self.fatal_error = _fatal_entry(event.exception)
        elif isinstance(event, events.EngineFinished):
            self.finished = event

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        from schemathesis.reporting._command import get_command_representation
        from schemathesis.reporting.html import write_report

        sanitization = ctx.config.output.sanitization
        sanitization_config = sanitization if sanitization.enabled else None
        summary = ctx.summary()
        errors = []
        for group in summary.errors:
            entry = self.errors.get(group.title)
            if entry is not None:
                entry.count = group.count
                errors.append(entry)
        data = ReportData(
            meta=ReportMeta(
                generated_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                location=_sanitized(self.location, sanitization_config),
                base_url=_sanitized(self.base_url, sanitization_config),
                command=get_command_representation(sanitization_config),
                seed=ctx.config.seed,
            ),
            summary=summary,
            errors=errors,
            fatal_error=self.fatal_error,
            running_time=running_time(self.started_at, self.finished),
            stop_reason=self.finished.stop_reason if self.finished is not None else StopReason.INTERRUPTED,
            started=self.started_at is not None,
            complete=self.finished is not None,
            exit_code=ctx.exit_code,
        )
        write_report(data, self.output_dir)


def _fatal_entry(exception: Exception) -> ErrorEntry:
    if isinstance(exception, LoaderError):
        message = "\n".join([exception.message, *exception.extras])
        return ErrorEntry(
            operations=[], title="Failed to load specification", message=message, traceback=None, phase=None
        )
    info = EngineErrorInfo(error=exception)
    return ErrorEntry(operations=[], title=info.title, message=info.message, traceback=None, phase=None)


def _sanitized(url: str | None, sanitization_config: SanitizationConfig | None) -> str | None:
    # The schema location may be a file path; only URLs can carry credentials.
    if url is None or sanitization_config is None or not url.startswith(("http://", "https://")):
        return url
    return sanitize_url(url, config=sanitization_config)
