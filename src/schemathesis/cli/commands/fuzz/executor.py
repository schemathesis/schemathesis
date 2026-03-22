from __future__ import annotations

from typing import Any

from schemathesis.cli.commands.fuzz.context import FuzzExecutionContext
from schemathesis.cli.commands.fuzz.handlers.output import FuzzOutputHandler
from schemathesis.cli.executor import execute_event_loop
from schemathesis.cli.loaders import into_event_stream
from schemathesis.config import FuzzConfig, ProjectConfig
from schemathesis.engine import from_schema
from schemathesis.engine.events import EventGenerator


def execute(
    *,
    location: str,
    config: ProjectConfig,
    filter_set: dict[str, Any],
    fuzz_config: FuzzConfig,
    args: list[str],
    params: dict[str, Any],
) -> None:
    event_stream = into_event_stream(
        location=location,
        config=config,
        filter_set=filter_set,
        engine_callback=lambda schema: from_schema(schema).fuzz(fuzz_config),
    )
    _execute(event_stream, config=config, args=args, params=params)


def _execute(
    event_stream: EventGenerator,
    *,
    config: ProjectConfig,
    args: list[str],
    params: dict[str, Any],
) -> None:
    execute_event_loop(
        event_stream,
        config=config,
        args=args,
        params=params,
        output_handler=FuzzOutputHandler(),
        context_factory=lambda cfg: FuzzExecutionContext(config=cfg),
    )
