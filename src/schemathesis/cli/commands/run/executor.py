from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable

import click

from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.cli.commands.run.events import LoadingFinished, LoadingStarted
from schemathesis.cli.commands.run.handlers import display_handler_error
from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.commands.run.handlers.cassettes import CassetteWriter
from schemathesis.cli.commands.run.handlers.junitxml import JunitXMLHandler
from schemathesis.cli.commands.run.handlers.output import OutputHandler
from schemathesis.cli.commands.run.loaders import AutodetectConfig, load_schema
from schemathesis.cli.commands.run.reports import ReportConfig, ReportFormat
from schemathesis.cli.ext.fs import open_file
from schemathesis.core.errors import LoaderError
from schemathesis.core.output import OutputConfig
from schemathesis.engine import from_schema
from schemathesis.engine.config import EngineConfig
from schemathesis.engine.events import EventGenerator, FatalError, Interrupted
from schemathesis.filters import FilterSet

CUSTOM_HANDLERS: list[type[EventHandler]] = []


def handler() -> Callable[[type], None]:
    """Register a new CLI event handler."""

    def _wrapper(cls: type) -> None:
        CUSTOM_HANDLERS.append(cls)

    return _wrapper


@dataclass
class RunConfig:
    location: str
    base_url: str | None
    filter_set: FilterSet
    engine: EngineConfig
    wait_for_schema: float | None
    rate_limit: str | None
    output: OutputConfig
    report: ReportConfig | None
    args: list[str]
    params: dict[str, Any]


def execute(config: RunConfig) -> None:
    event_stream = into_event_stream(config)
    _execute(event_stream, config)


def into_event_stream(config: RunConfig) -> EventGenerator:
    loader_config = AutodetectConfig(
        location=config.location,
        network=config.engine.network,
        wait_for_schema=config.wait_for_schema,
        base_url=config.base_url,
        rate_limit=config.rate_limit,
        output=config.output,
        generation=config.engine.execution.generation,
    )
    loading_started = LoadingStarted(location=config.location)
    yield loading_started

    try:
        schema = load_schema(loader_config)
        schema.filter_set = config.filter_set
    except KeyboardInterrupt:
        yield Interrupted(phase=None)
        return
    except LoaderError as exc:
        yield FatalError(exception=exc)
        return

    yield LoadingFinished(
        location=config.location,
        start_time=loading_started.timestamp,
        base_url=schema.get_base_url(),
        specification=schema.specification,
        statistic=schema.statistic,
        schema=schema.raw_schema,
        base_path=schema.base_path,
    )

    try:
        yield from from_schema(schema, config=config.engine).execute()
    except Exception as exc:
        yield FatalError(exception=exc)


def initialize_handlers(config: RunConfig) -> list[EventHandler]:
    """Create event handlers based on run configuration."""
    handlers: list[EventHandler] = []

    if config.report is not None:
        if ReportFormat.JUNIT in config.report.formats:
            path = config.report.get_path(ReportFormat.JUNIT)
            open_file(path)
            handlers.append(JunitXMLHandler(path))

        for format in (ReportFormat.VCR, ReportFormat.HAR):
            if format in config.report.formats:
                path = config.report.get_path(format)
                open_file(path)
                handlers.append(
                    CassetteWriter(
                        format=format,
                        path=path,
                        sanitize_output=config.report.sanitize_output,
                        preserve_bytes=config.report.preserve_bytes,
                    )
                )

    for custom_handler in CUSTOM_HANDLERS:
        handlers.append(custom_handler(*config.args, **config.params))

    handlers.append(
        OutputHandler(
            workers_num=config.engine.execution.workers_num,
            seed=config.engine.execution.seed,
            rate_limit=config.rate_limit,
            wait_for_schema=config.wait_for_schema,
            engine_config=config.engine,
            report_config=config.report,
        )
    )

    return handlers


def _execute(event_stream: EventGenerator, config: RunConfig) -> None:
    handlers = initialize_handlers(config)
    ctx = ExecutionContext(output_config=config.output, seed=config.engine.execution.seed)

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
