from __future__ import annotations

import sys
from typing import Any, Callable

import click

from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.cli.commands.run.events import LoadingFinished, LoadingStarted
from schemathesis.cli.commands.run.handlers import display_handler_error
from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.commands.run.handlers.cassettes import CassetteWriter
from schemathesis.cli.commands.run.handlers.junitxml import JunitXMLHandler
from schemathesis.cli.commands.run.handlers.output import OutputHandler
from schemathesis.cli.commands.run.loaders import load_schema
from schemathesis.cli.ext.fs import open_file
from schemathesis.config import ReportFormat, SchemathesisConfig
from schemathesis.core.errors import LoaderError
from schemathesis.engine import from_schema
from schemathesis.engine.events import EventGenerator, FatalError, Interrupted

CUSTOM_HANDLERS: list[type[EventHandler]] = []


def handler() -> Callable[[type], None]:
    """Register a new CLI event handler."""

    def _wrapper(cls: type) -> None:
        CUSTOM_HANDLERS.append(cls)

    return _wrapper


def execute(
    *,
    location: str,
    config: SchemathesisConfig,
    args: list[str],
    params: dict[str, Any],
) -> None:
    event_stream = into_event_stream(location=location, config=config)
    _execute(event_stream, config=config, args=args, params=params)


def into_event_stream(*, location: str, config: SchemathesisConfig) -> EventGenerator:
    loading_started = LoadingStarted(location=location)
    yield loading_started

    try:
        schema = load_schema(location=location, config=config)
        # TODO: extract filters
        # schema.filter_set = config.filter_set
    except KeyboardInterrupt:
        yield Interrupted(phase=None)
        return
    except LoaderError as exc:
        yield FatalError(exception=exc)
        return

    yield LoadingFinished(
        location=location,
        start_time=loading_started.timestamp,
        base_url=schema.get_base_url(),
        specification=schema.specification,
        statistic=schema.statistic,
        schema=schema.raw_schema,
        base_path=schema.base_path,
    )

    try:
        yield from from_schema(schema, config=config).execute()
    except Exception as exc:
        yield FatalError(exception=exc)


def initialize_handlers(
    *,
    config: SchemathesisConfig,
    args: list[str],
    params: dict[str, Any],
) -> list[EventHandler]:
    """Create event handlers based on run configuration."""
    handlers: list[EventHandler] = []

    if config.reports.junit.enabled:
        path = config.reports.get_path(ReportFormat.JUNIT)
        open_file(path)
        handlers.append(JunitXMLHandler(path))
    for format, report in (
        (ReportFormat.VCR, config.reports.vcr),
        (ReportFormat.HAR, config.reports.har),
    ):
        if report.enabled:
            path = config.reports.get_path(format)
            open_file(path)
            handlers.append(
                CassetteWriter(
                    format=format,
                    path=path,
                    sanitize_output=config.output.sanitize,
                    preserve_bytes=config.reports.preserve_bytes,
                )
            )

    for custom_handler in CUSTOM_HANDLERS:
        handlers.append(custom_handler(*args, **params))

    handlers.append(OutputHandler(config=config))

    return handlers


def _execute(
    event_stream: EventGenerator,
    *,
    config: SchemathesisConfig,
    args: list[str],
    params: dict[str, Any],
) -> None:
    handlers = initialize_handlers(config=config, args=args, params=params)
    ctx = ExecutionContext(config=config)

    def shutdown() -> None:
        for _handler in handlers:
            _handler.shutdown(ctx)

    for handler in handlers:
        handler.start(ctx)

    try:
        for event in event_stream:
            ctx.on_event(event)
            for handler in handlers:
                try:
                    handler.handle_event(ctx, event)
                except Exception as exc:
                    # `Abort` is used for handled errors
                    if not isinstance(exc, click.Abort):
                        display_handler_error(handler, exc)
                    raise
    except Exception as exc:
        if isinstance(exc, click.Abort):
            # To avoid showing "Aborted!" message, which is the default behavior in Click
            sys.exit(1)
        raise
    finally:
        shutdown()
    sys.exit(ctx.exit_code)
