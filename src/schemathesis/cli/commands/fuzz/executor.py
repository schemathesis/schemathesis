from __future__ import annotations

import sys
from typing import Any

import click

from schemathesis.cli.commands.fuzz.context import FuzzExecutionContext
from schemathesis.cli.commands.fuzz.handlers.output import FuzzOutputHandler
from schemathesis.cli.constants import MISSING_BASE_URL_MESSAGE
from schemathesis.cli.events import LoadingFinished, LoadingStarted
from schemathesis.cli.loaders import load_schema
from schemathesis.config import FuzzConfig, ProjectConfig
from schemathesis.core.errors import LoaderError
from schemathesis.core.fs import file_exists
from schemathesis.engine import from_schema
from schemathesis.engine.events import EventGenerator, FatalError, Interrupted


def into_event_stream(
    *, location: str, config: ProjectConfig, filter_set: dict[str, Any], fuzz_config: FuzzConfig
) -> EventGenerator:
    loading_started = LoadingStarted(location=location)
    yield loading_started

    try:
        schema = load_schema(location=location, config=config)
        if file_exists(location) and schema.config.base_url is None:
            raise click.UsageError(MISSING_BASE_URL_MESSAGE)
        schema.filter_set = schema.config.operations.create_filter_set(**filter_set)
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
        config=schema.config,
        base_path=schema.base_path,
        find_operation_by_label=schema.find_operation_by_label,
    )

    try:
        yield from from_schema(schema).fuzz(fuzz_config)
    except Exception as exc:
        yield FatalError(exception=exc)


def execute(*, location: str, config: ProjectConfig, filter_set: dict[str, Any], fuzz_config: FuzzConfig) -> None:
    event_stream = into_event_stream(location=location, config=config, filter_set=filter_set, fuzz_config=fuzz_config)
    _execute(event_stream, config=config)


def _execute(event_stream: EventGenerator, *, config: ProjectConfig) -> None:
    ctx: FuzzExecutionContext | None = None
    handler = FuzzOutputHandler()

    def shutdown() -> None:
        if ctx is not None:
            handler.shutdown(ctx)

    try:
        ctx = FuzzExecutionContext(config=config)
        handler.start(ctx)

        for event in event_stream:
            ctx.on_event(event)
            try:
                handler.handle_event(ctx, event)
            except click.Abort:
                raise
            except Exception:
                raise

    except (click.Abort, KeyboardInterrupt):
        # Suppress click's "Aborted!" message — KeyboardInterrupt can escape the generator
        # when the main thread is processing an event outside run_forever's try/except.
        sys.exit(1)
    finally:
        shutdown()

    sys.exit(ctx.exit_code if ctx is not None else 1)
