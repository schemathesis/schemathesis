"""Shared handler initialization and error display used by both `st run` and `st fuzz`."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

import click

from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.commands.run.handlers.har import HarHandler
from schemathesis.cli.commands.run.handlers.junitxml import JunitXMLHandler
from schemathesis.cli.commands.run.handlers.ndjson import NdjsonHandler
from schemathesis.cli.commands.run.handlers.output import OutputHandler
from schemathesis.cli.commands.run.handlers.vcr import VcrHandler
from schemathesis.cli.constants import EXTENSIONS_DOCUMENTATION_URL, ISSUE_TRACKER_URL
from schemathesis.cli.ext.fs import open_file
from schemathesis.cli.ext.handlers import CUSTOM_HANDLERS
from schemathesis.config import ReportFormat
from schemathesis.core.errors import format_exception

if TYPE_CHECKING:
    from schemathesis.config import ProjectConfig
    from schemathesis.engine.events import EngineEvent, EventGenerator


class ExecutionContext(Protocol):
    exit_code: int

    def on_event(self, event: EngineEvent) -> None: ...


try:
    from schemathesis.cli.commands.run.handlers.allure import AllureHandler

    _BUILT_IN_HANDLERS: tuple[type[EventHandler], ...] = (
        VcrHandler,
        HarHandler,
        JunitXMLHandler,
        NdjsonHandler,
        OutputHandler,
        AllureHandler,
    )
except ImportError:
    _BUILT_IN_HANDLERS = (VcrHandler, HarHandler, JunitXMLHandler, NdjsonHandler, OutputHandler)


def is_built_in_handler(handler: EventHandler) -> bool:
    return type(handler) in _BUILT_IN_HANDLERS


def initialize_report_handlers(
    *,
    config: ProjectConfig,
    args: list[str],
    params: dict[str, Any],
) -> list[EventHandler]:
    """Initialize report handlers (JUnit, VCR, HAR, NDJSON, Allure) and custom handlers."""
    handlers: list[EventHandler] = []

    if config.reports.junit.enabled:
        path = config.reports.get_path(ReportFormat.JUNIT)
        open_file(path)
        handlers.append(JunitXMLHandler(path, group_by=config.reports.group_by))
    if config.reports.vcr.enabled:
        path = config.reports.get_path(ReportFormat.VCR)
        open_file(path)
        handlers.append(VcrHandler(output=path, config=config.output, preserve_bytes=config.reports.preserve_bytes))
    if config.reports.har.enabled:
        path = config.reports.get_path(ReportFormat.HAR)
        open_file(path)
        handlers.append(HarHandler(output=path, config=config.output, preserve_bytes=config.reports.preserve_bytes))
    if config.reports.ndjson.enabled:
        path = config.reports.get_path(ReportFormat.NDJSON)
        open_file(path)
        handlers.append(NdjsonHandler(output=path, config=config))
    if config.reports.allure.enabled:
        try:
            from schemathesis.cli.commands.run.handlers.allure import AllureHandler
        except ImportError as exc:
            raise click.ClickException(str(exc)) from exc
        allure_path = config.reports.get_path(ReportFormat.ALLURE)
        handlers.append(AllureHandler(output_dir=allure_path, config=config.output))

    for custom_handler in CUSTOM_HANDLERS:
        handlers.append(custom_handler(*args, **params))

    return handlers


def display_handler_error(handler: EventHandler, exc: Exception) -> None:
    """Display an error that occurred within an event handler."""
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
    context_factory: Callable[[ProjectConfig], ExecutionContext],
) -> None:
    handlers = [*initialize_report_handlers(config=config, args=args, params=params), output_handler]
    ctx: ExecutionContext | None = None

    def shutdown() -> None:
        if ctx is not None:
            for h in handlers:
                h.shutdown(ctx)

    try:
        ctx = context_factory(config)
        for h in handlers:
            h.start(ctx)

        for event in event_stream:
            ctx.on_event(event)
            for h in handlers:
                try:
                    h.handle_event(ctx, event)
                except Exception as exc:
                    if not isinstance(exc, click.Abort):
                        display_handler_error(h, exc)
                        raise click.Abort() from exc
                    raise

    except (click.Abort, KeyboardInterrupt):
        sys.exit(1)
    finally:
        shutdown()

    sys.exit(ctx.exit_code if ctx is not None else 1)
