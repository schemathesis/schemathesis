from __future__ import annotations

from typing import Any

from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.cli.commands.run.handlers.output import OutputHandler
from schemathesis.cli.executor import execute_event_loop
from schemathesis.cli.loaders import into_event_stream
from schemathesis.config import ProjectConfig
from schemathesis.engine import from_schema
from schemathesis.engine.events import EventGenerator


def execute(
    *,
    location: str,
    config: ProjectConfig,
    filter_set: dict[str, Any],
    args: list[str],
    params: dict[str, Any],
) -> None:
    event_stream = into_event_stream(
        location=location,
        config=config,
        filter_set=filter_set,
        engine_callback=lambda schema: from_schema(schema).execute(),
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
        output_handler=OutputHandler(config=config),
        context_factory=lambda cfg: ExecutionContext(
            config=cfg,
            baseline_update=params.get("baseline_update", False),
            baseline_prune=params.get("baseline_prune", False),
        ),
    )
