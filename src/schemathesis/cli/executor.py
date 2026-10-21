"""Shared handler initialization and error display used by both `st run` and `st fuzz`."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from schemathesis.cli.commands.fuzz.handlers.output import FuzzOutputHandler
from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.commands.run.handlers.crashes import CrashHandler
from schemathesis.cli.commands.run.handlers.har import HarHandler
from schemathesis.cli.commands.run.handlers.html import HtmlReportHandler
from schemathesis.cli.commands.run.handlers.junitxml import JunitXMLHandler
from schemathesis.cli.commands.run.handlers.ndjson import NdjsonHandler
from schemathesis.cli.commands.run.handlers.output import OutputHandler
from schemathesis.cli.commands.run.handlers.vcr import VcrHandler
from schemathesis.cli.constants import EXTENSIONS_DOCUMENTATION_URL, ISSUE_TRACKER_URL, ExitCode
from schemathesis.cli.ext.fs import load_baseline, open_file, prepare_directory
from schemathesis.cli.ext.handlers import CUSTOM_HANDLERS
from schemathesis.cli.json_report import JsonReportHandler
from schemathesis.cli.pytest_report import PytestReportHandler
from schemathesis.cli.wfc_report import WfcReportHandler
from schemathesis.config import DEFAULT_REPORT_DIRECTORY, ReportFormat
from schemathesis.core.errors import format_exception

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.config import ProjectConfig
    from schemathesis.engine.events import EventGenerator


_REPORT_HANDLER_FORMATS: dict[type[EventHandler], ReportFormat] = {
    VcrHandler: ReportFormat.VCR,
    HarHandler: ReportFormat.HAR,
    JunitXMLHandler: ReportFormat.JUNIT,
    NdjsonHandler: ReportFormat.NDJSON,
    JsonReportHandler: ReportFormat.JSON,
    WfcReportHandler: ReportFormat.WFC,
    PytestReportHandler: ReportFormat.PYTEST,
    HtmlReportHandler: ReportFormat.HTML,
}
try:
    from schemathesis.cli.commands.run.handlers.allure import AllureHandler

    _REPORT_HANDLER_FORMATS[AllureHandler] = ReportFormat.ALLURE
except ImportError:
    pass

_BUILT_IN_HANDLERS: tuple[type[EventHandler], ...] = (
    *_REPORT_HANDLER_FORMATS,
    OutputHandler,
    FuzzOutputHandler,
    CrashHandler,
)


def is_built_in_handler(handler: EventHandler) -> bool:
    return type(handler) in _BUILT_IN_HANDLERS


def _report_option(config: ProjectConfig, params: dict[str, Any], format: ReportFormat) -> str | None:
    """CLI option that set the report location, if any."""
    if getattr(config.reports, format.value).path is not None:
        return f"'--report-{format.value}-path'" if params.get(f"report_{format.value}_path") is not None else None
    if Path(params.get("report_directory", DEFAULT_REPORT_DIRECTORY)) != DEFAULT_REPORT_DIRECTORY:
        return "'--report-dir'"
    return None


def initialize_report_handlers(
    *,
    config: ProjectConfig,
    args: list[str],
    params: dict[str, Any],
) -> list[EventHandler]:
    """Initialize built-in report handlers and custom handlers."""
    handlers: list[EventHandler] = []

    file_reports: tuple[tuple[ReportFormat, Callable[[Path], EventHandler]], ...] = (
        (ReportFormat.JUNIT, JunitXMLHandler),
        (
            ReportFormat.VCR,
            lambda path: VcrHandler(output=path, config=config.output, preserve_bytes=config.reports.preserve_bytes),
        ),
        (
            ReportFormat.HAR,
            lambda path: HarHandler(output=path, config=config.output, preserve_bytes=config.reports.preserve_bytes),
        ),
        (ReportFormat.NDJSON, lambda path: NdjsonHandler(output=path, config=config)),
        (ReportFormat.JSON, JsonReportHandler),
        (ReportFormat.WFC, WfcReportHandler),
        (ReportFormat.PYTEST, PytestReportHandler),
    )
    for report_format, make_handler in file_reports:
        if getattr(config.reports, report_format.value).enabled:
            path = config.reports.get_path(report_format)
            open_file(path, _report_option(config, params, report_format))
            handlers.append(make_handler(path))
    if config.reports.allure.enabled:
        try:
            from schemathesis.cli.commands.run.handlers.allure import AllureHandler
        except ImportError as exc:
            raise click.ClickException(str(exc)) from exc
        allure_path = config.reports.get_path(ReportFormat.ALLURE)
        prepare_directory(allure_path, _report_option(config, params, ReportFormat.ALLURE))
        handlers.append(AllureHandler(output_dir=allure_path, config=config.output))
    if config.reports.html.enabled:
        html_path = config.reports.get_path(ReportFormat.HTML)
        prepare_directory(html_path, _report_option(config, params, ReportFormat.HTML))
        handlers.append(HtmlReportHandler(html_path))

    if config.cache.enabled:
        handlers.append(
            CrashHandler(
                cache_directory=config.cache.directory,
                schema_location=params.get("location") or "",
                base_url=config.base_url or "",
                tls_verify=config.tls_verify,
                sanitization=config.output.sanitization,
            )
        )

    for custom_handler in CUSTOM_HANDLERS:
        handlers.append(custom_handler(*args, **params))

    return handlers


def display_handler_error(handler: EventHandler, exc: Exception, config: ProjectConfig) -> None:
    """Display an error that occurred within an event handler."""
    report_format = _REPORT_HANDLER_FORMATS.get(type(handler))
    if report_format is not None and isinstance(exc, OSError):
        path = config.reports.get_path(report_format)
        click.secho(
            f"Failed to write {report_format.display_name} report to {path}: {exc.strerror or str(exc)}", fg="red"
        )
        return
    is_built_in = is_built_in_handler(handler)
    if is_built_in:
        title = "Internal Error"
        intro = "\nSchemathesis encountered an unexpected issue."
        message = format_exception(exc, with_traceback=True)
        footer = (
            f"\nWe apologize for the inconvenience. This appears to be an internal issue.\n"
            f"Please consider reporting this error to our issue tracker:\n\n  {ISSUE_TRACKER_URL}."
        )
    else:
        title = "CLI Handler Error"
        intro = f"\nAn error occurred within your custom CLI handler `{click.style(handler.__class__.__name__, bold=True)}`."
        message = format_exception(exc, with_traceback=True, skip_frames=1)
        footer = f"\nFor more information on implementing extensions for Schemathesis CLI, visit {EXTENSIONS_DOCUMENTATION_URL}"
    click.secho(title, fg="red", bold=True)
    click.echo(intro)
    click.secho(f"\n{message}", fg="red")
    click.echo(footer)


def execute_event_loop(
    event_stream: EventGenerator,
    *,
    config: ProjectConfig,
    args: list[str],
    params: dict[str, Any],
    output_handler: EventHandler,
    context_factory: Callable[[ProjectConfig], BaseExecutionContext],
) -> None:
    from rich.text import Text

    # Warm Rich's lazy emoji-codes import on the main thread; it is not concurrency-safe.
    Text.from_markup("")

    load_baseline(config)
    handlers = [*initialize_report_handlers(config=config, args=args, params=params), output_handler]
    ctx: BaseExecutionContext | None = None
    # Set when the loop ends abnormally; the process and the reports use it instead of the run's verdict.
    abnormal_exit_code: int | None = None

    def shutdown() -> None:
        if ctx is not None:
            if abnormal_exit_code is not None:
                ctx.exit_code = abnormal_exit_code
            for h in handlers:
                # The exit code is the API's verdict; a reporter failing at shutdown must not mask it,
                # unlike a mid-run handler error, which aborts.
                try:
                    h.shutdown(ctx)
                except Exception as exc:
                    display_handler_error(h, exc, config)

    try:
        ctx = context_factory(config)
        for h in handlers:
            try:
                h.start(ctx)
            except Exception as exc:
                if not isinstance(exc, click.Abort):
                    display_handler_error(h, exc, config)
                    raise click.Abort() from exc
                raise

        for event in event_stream:
            ctx.on_event(event)
            for h in handlers:
                try:
                    h.handle_event(ctx, event)
                except Exception as exc:
                    if not isinstance(exc, click.Abort):
                        display_handler_error(h, exc, config)
                        raise click.Abort() from exc
                    raise

    except click.Abort:
        # A fatal error means the run could not do its job, the same class as a usage error.
        abnormal_exit_code = ExitCode.ERROR
    except KeyboardInterrupt:
        abnormal_exit_code = ExitCode.INTERRUPTED
    except click.ClickException as exc:
        abnormal_exit_code = exc.exit_code
        raise
    except Exception:
        # The interpreter exits with 1 on an uncaught exception.
        abnormal_exit_code = 1
        raise
    finally:
        try:
            event_stream.close()
        finally:
            shutdown()

    if abnormal_exit_code is not None:
        sys.exit(abnormal_exit_code)
    assert ctx is not None
    sys.exit(ctx.exit_code)
