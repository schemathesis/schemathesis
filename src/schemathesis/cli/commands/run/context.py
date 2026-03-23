from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass, field

from schemathesis.cli.commands.run.events import LoadingFinished
from schemathesis.config import ProjectConfig
from schemathesis.engine import Status, events
from schemathesis.engine.statistic import Statistic
from schemathesis.schemas import APIOperation


@dataclass
class ExecutionContext:
    """Storage for the current context of the execution."""

    config: ProjectConfig
    find_operation_by_label: Callable[[str], APIOperation | None] | None = None
    statistic: Statistic = field(default_factory=Statistic)
    exit_code: int = 0
    initialization_lines: list[str | Generator[str, None, None]] = field(default_factory=list)
    summary_lines: list[str | Generator[str, None, None]] = field(default_factory=list)

    def add_initialization_line(self, line: str | Generator[str, None, None]) -> None:
        self.initialization_lines.append(line)

    def add_summary_line(self, line: str | Generator[str, None, None]) -> None:
        self.summary_lines.append(line)

    def on_event(self, event: events.EngineEvent) -> None:
        if isinstance(event, LoadingFinished):
            self.find_operation_by_label = event.find_operation_by_label
        if isinstance(event, events.ScenarioFinished):
            self.statistic.on_scenario_finished(event.recorder)
        elif isinstance(event, events.NonFatalError) or (
            isinstance(event, events.PhaseFinished)
            and event.phase.is_enabled
            and event.status in (Status.FAILURE, Status.ERROR)
        ):
            self.exit_code = 1
