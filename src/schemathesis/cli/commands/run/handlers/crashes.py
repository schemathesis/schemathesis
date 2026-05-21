from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import WRITER_WORKER_JOIN_TIMEOUT, EventHandler
from schemathesis.cli.events import LoadingFinished
from schemathesis.core.cache.io import effective_directory
from schemathesis.engine import Status, events
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.reporting.crashes import CrashWriter, build_crashes_from_recorder

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.config import ProjectConfig, SanitizationConfig


def _project_title(raw_schema: dict, config: ProjectConfig) -> str | None:
    info = raw_schema.get("info")
    title = info.get("title") if isinstance(info, dict) else None
    if isinstance(title, str) and title in config._get_parent().projects.named:
        return title
    return None


class CrashHandler(EventHandler):
    """CLI event handler that writes crash reproduction files on test failures."""

    __slots__ = ("cache_directory", "schema_location", "base_url", "sanitization", "queue", "worker")

    def __init__(
        self,
        *,
        cache_directory: Path | None,
        schema_location: str,
        base_url: str,
        sanitization: SanitizationConfig,
    ) -> None:
        self.cache_directory = cache_directory
        self.schema_location = schema_location
        self.base_url = base_url
        self.sanitization = sanitization
        self.queue: Queue[_Process | None] = Queue()
        self.worker: threading.Thread | None = None

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, LoadingFinished):
            self._start_worker(_project_title(event.schema, event.config))
        elif isinstance(event, (events.ScenarioFinished, events.FuzzScenarioFinished)):
            if event.status in (Status.FAILURE, Status.ERROR, Status.SUCCESS):
                self.queue.put(_Process(recorder=event.recorder, success=event.status == Status.SUCCESS))

    def _start_worker(self, project_title: str | None) -> None:
        assert self.worker is None
        directory = effective_directory(self.cache_directory, project_title) / "crashes"
        self.worker = threading.Thread(
            name="SchemathesisCrashWriter",
            target=_run,
            kwargs={
                "directory": directory,
                "schema_location": self.schema_location,
                "base_url": self.base_url,
                "sanitization": self.sanitization,
                "queue": self.queue,
            },
        )
        self.worker.start()

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        self.queue.put(None)
        if self.worker is not None:
            self.worker.join(WRITER_WORKER_JOIN_TIMEOUT)


def _run(
    directory: Path,
    schema_location: str,
    base_url: str,
    sanitization: SanitizationConfig,
    queue: Queue[_Process | None],
) -> None:
    writer = CrashWriter(directory=directory)
    writer.open(schema_location=schema_location, base_url=base_url)

    failed: set[str] = set()
    succeeded: set[str] = set()
    while True:
        item = queue.get()
        if item is None:
            break
        if not item.success:
            failing_case_ids = _find_failing_case_ids(item.recorder)
            failed |= _operation_labels(item.recorder, failing_case_ids)
            for failing_case_id in failing_case_ids:
                for crash in build_crashes_from_recorder(
                    recorder=item.recorder,
                    failing_case_id=failing_case_id,
                    sanitization=sanitization,
                ):
                    writer.write(crash)
        else:
            succeeded |= _operation_labels(item.recorder, item.recorder.cases.keys())

    for operation in succeeded - failed:
        writer.remove_by_operation(operation)


def _find_failing_case_ids(recorder: ScenarioRecorder) -> list[str]:
    return [case_id for case_id, checks in recorder.checks.items() if any(c.status == Status.FAILURE for c in checks)]


def _operation_labels(recorder: ScenarioRecorder, case_ids: Iterable[str]) -> set[str]:
    labels: set[str] = set()
    for case_id in case_ids:
        node = recorder.cases[case_id]
        labels.add(node.value.operation.label)
    return labels


@dataclass(slots=True)
class _Process:
    recorder: ScenarioRecorder
    success: bool
