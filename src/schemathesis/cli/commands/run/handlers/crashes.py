from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import BackgroundWriterHandler
from schemathesis.cli.events import LoadingFinished
from schemathesis.core.cache.io import effective_directory
from schemathesis.engine import Status, events
from schemathesis.engine.recorder import RecordedScenario
from schemathesis.reporting.crashes import CrashWriter, build_crashes_from_recorder

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.config import SanitizationConfig


class CrashHandler(BackgroundWriterHandler["_Process"]):
    """CLI event handler that writes crash reproduction files on test failures."""

    __slots__ = ("cache_directory", "schema_location", "base_url", "tls_verify", "sanitization")

    def __init__(
        self,
        *,
        cache_directory: Path | None,
        schema_location: str,
        base_url: str,
        tls_verify: bool | str | None,
        sanitization: SanitizationConfig,
    ) -> None:
        super().__init__()
        self.cache_directory = cache_directory
        self.schema_location = schema_location
        self.base_url = base_url
        self.tls_verify = tls_verify
        self.sanitization = sanitization

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, LoadingFinished):
            self._start_worker(event.config.named_project_title(event.title))
        elif isinstance(event, (events.ScenarioFinished, events.FuzzScenarioFinished)) and event.status in (
            Status.FAILURE,
            Status.ERROR,
            Status.SUCCESS,
        ):
            self.queue.put(_Process(recorder=event.recorder, success=event.status == Status.SUCCESS))

    def _start_worker(self, project_title: str | None) -> None:
        assert self.worker is None
        directory = effective_directory(self.cache_directory, project_title) / "crashes"
        self._start_writer(
            "SchemathesisCrashWriter",
            _run,
            directory=directory,
            schema_location=self.schema_location,
            base_url=self.base_url,
            tls_verify=self.tls_verify,
            sanitization=self.sanitization,
        )


def _run(
    directory: Path,
    schema_location: str,
    base_url: str,
    tls_verify: bool | str | None,
    sanitization: SanitizationConfig,
    queue: Queue[_Process | None],
) -> None:
    try:
        writer = CrashWriter(directory=directory)
        writer.open(schema_location=schema_location, base_url=base_url, tls_verify=tls_verify)

        failed: set[str] = set()
        succeeded: set[str] = set()
        while True:
            item = queue.get()
            if item is None:
                break
            if not item.success:
                failing_case_ids = item.recorder.failing_case_ids()
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
    except OSError:
        pass


def _operation_labels(recorder: RecordedScenario, case_ids: Iterable[str]) -> set[str]:
    labels: set[str] = set()
    for case_id in case_ids:
        node = recorder.cases[case_id]
        labels.add(node.value.operation.label)
    return labels


@dataclass(slots=True)
class _Process:
    recorder: RecordedScenario
    success: bool
