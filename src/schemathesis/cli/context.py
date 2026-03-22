from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from schemathesis.cli.events import LoadingFinished
from schemathesis.engine.statistic import Statistic

if TYPE_CHECKING:
    from schemathesis.config import ProjectConfig
    from schemathesis.engine import events
    from schemathesis.schemas import APIOperation


@dataclass
class BaseExecutionContext:
    """Shared execution state for CLI commands (run, fuzz)."""

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
