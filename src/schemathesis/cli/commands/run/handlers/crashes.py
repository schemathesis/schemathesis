from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import WRITER_WORKER_JOIN_TIMEOUT, EventHandler
from schemathesis.engine import Status, events
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.reporting.crashes import CrashWriter, build_crashes_from_recorder

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.config import SanitizationConfig


@dataclass(slots=True)
class CrashHandler(EventHandler):
    """CLI event handler that writes crash reproduction files on test failures."""

    directory: Path
    schema_location: str
    base_url: str
    sanitization: SanitizationConfig
    queue: Queue[_ProcessFailure | _ProcessSuccess | _Finalize]
    worker: threading.Thread

    def __init__(
        self,
        *,
        directory: Path,
        schema_location: str,
        base_url: str,
        sanitization: SanitizationConfig,
    ) -> None:
        self.directory = directory
        self.schema_location = schema_location
        self.base_url = base_url
        self.sanitization = sanitization
        self.queue = Queue()
        self.worker = threading.Thread(
            name="SchemathesisCrashWriter",
            target=_run,
            kwargs={
                "directory": self.directory,
                "schema_location": self.schema_location,
                "base_url": self.base_url,
                "sanitization": self.sanitization,
                "queue": self.queue,
            },
        )
        self.worker.start()

    def start(self, ctx: BaseExecutionContext) -> None:
        pass

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, events.ScenarioFinished):
            if event.status == Status.FAILURE:
                self.queue.put(_ProcessFailure(recorder=event.recorder))
            elif event.status == Status.SUCCESS:
                self.queue.put(_ProcessSuccess(operation=event.label or ""))
        elif isinstance(event, events.FuzzScenarioFinished):
            if event.status == Status.FAILURE:
                self.queue.put(_ProcessFailure(recorder=event.recorder))
            elif event.status == Status.SUCCESS:
                self.queue.put(_ProcessSuccess(operation=event.recorder.label))

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        self.queue.put(_Finalize())
        self.worker.join(WRITER_WORKER_JOIN_TIMEOUT)


def _run(
    directory: Path,
    schema_location: str,
    base_url: str,
    sanitization: SanitizationConfig,
    queue: Queue[_ProcessFailure | _ProcessSuccess | _Finalize],
) -> None:
    writer = CrashWriter(directory=directory)
    writer.open(schema_location=schema_location, base_url=base_url)

    while True:
        item = queue.get()
        if isinstance(item, _ProcessFailure):
            failing_case_id = _find_failing_case_id(item.recorder)
            if failing_case_id is not None:
                for crash in build_crashes_from_recorder(
                    recorder=item.recorder,
                    failing_case_id=failing_case_id,
                    sanitization=sanitization,
                ):
                    writer.write(crash)
        elif isinstance(item, _ProcessSuccess):
            writer.remove_by_operation(item.operation)
        else:
            break


def _find_failing_case_id(recorder: ScenarioRecorder) -> str | None:
    for case_id, checks in recorder.checks.items():
        if any(c.status == Status.FAILURE for c in checks):
            return case_id
    return None


@dataclass(slots=True)
class _ProcessFailure:
    recorder: ScenarioRecorder


@dataclass(slots=True)
class _ProcessSuccess:
    operation: str


@dataclass(slots=True)
class _Finalize:
    pass
