from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.core.timing import format_timestamp
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine import StopReason
from schemathesis.engine.events import EngineFinished, EngineStarted
from schemathesis.engine.run import PhaseName

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.cli.summary import SummaryData
    from schemathesis.engine import events

# Internal phases are not part of the reported test phases, matching the terminal.
INTERNAL_PHASES = (PhaseName.PROBING, PhaseName.SCHEMA_ANALYSIS)


def _running_time(started_at: float | None, finished: events.EngineFinished | None) -> float | None:
    if finished is not None:
        return finished.running_time
    if started_at is None:
        return None
    return time.time() - started_at


def build_document(
    *,
    summary: SummaryData,
    command: str,
    seed: int | None,
    started_at: float | None,
    finished: events.EngineFinished | None,
    exit_code: int,
) -> dict[str, Any]:
    """`started_at` is `None` when the engine never started, `finished` when it never stopped."""
    operations = summary.operations
    payload = finished.payload if finished is not None else None
    return {
        "schemathesis_version": SCHEMATHESIS_VERSION,
        "command": command,
        "seed": seed,
        "started_at": format_timestamp(started_at) if started_at is not None else None,
        "running_time": _running_time(started_at, finished),
        "stop_reason": (finished.stop_reason if finished is not None else StopReason.INTERRUPTED).value,
        "complete": finished is not None,
        "exit_code": exit_code,
        "operations": {
            "total": operations.total,
            "selected": operations.selected,
            "tested": operations.tested,
            "errored": operations.errored,
            "skipped": operations.skipped,
            "skip_reasons": operations.skip_reasons,
        }
        if operations is not None
        else None,
        "phases": {
            phase.value: {"status": status.value, "skip_reason": reason.value if reason is not None else None}
            for phase, (status, reason) in summary.phases.items()
            if phase not in INTERNAL_PHASES
        }
        or None,
        "test_cases": {
            "generated": summary.test_cases.generated,
            "with_failures": summary.test_cases.with_failures,
            "unique_failures": summary.test_cases.unique_failures,
            "without_checks": summary.test_cases.without_checks,
        },
        "failures": [
            {
                "type": group.type,
                "title": group.title,
                "severity": group.severity.value,
                "count": group.count,
                "operations": group.operations,
            }
            for group in summary.failures
        ],
        "errors": [{"title": group.title, "count": group.count} for group in summary.errors],
        "warnings": summary.warnings.as_labels(),
        "auth": {
            "reauth_count": payload.reauth_count if payload is not None else 0,
            "reauth_broke": payload.reauth_broke if payload is not None else False,
        },
    }


class JsonReportHandler(EventHandler["BaseExecutionContext"]):
    """Writes the run's verdict as one JSON document."""

    __slots__ = ("output", "started_at", "finished")

    def __init__(self, output: Path) -> None:
        self.output = output
        self.started_at: float | None = None
        self.finished: events.EngineFinished | None = None

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, EngineStarted):
            # `running_time` measures the engine, so anchor `started_at` to the same point.
            self.started_at = event.timestamp
        elif isinstance(event, EngineFinished):
            self.finished = event

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        from schemathesis.reporting._command import get_command_representation

        sanitization = ctx.config.output.sanitization
        document = build_document(
            summary=ctx.summary(),
            command=get_command_representation(sanitization if sanitization.enabled else None),
            seed=ctx.config.seed,
            started_at=self.started_at,
            finished=self.finished,
            exit_code=ctx.exit_code,
        )
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
