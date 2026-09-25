from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from schemathesis.cli.events import LoadingFinished
from schemathesis.cli.summary import SummaryData
from schemathesis.engine import StopReason
from schemathesis.engine.statistic import Statistic

if TYPE_CHECKING:
    from schemathesis.config import ProjectConfig
    from schemathesis.core import Specification
    from schemathesis.core.statistic import ApiStatistic
    from schemathesis.engine import events
    from schemathesis.schemas import APIOperation


@dataclass
class BaseExecutionContext:
    """Shared execution state for CLI commands (run, fuzz)."""

    config: ProjectConfig
    find_operation_by_label: Callable[[str], APIOperation | None] | None = None
    specification: Specification | None = None
    api_statistic: ApiStatistic | None = None
    statistic: Statistic = field(default_factory=Statistic)
    exit_code: int = 0
    # Why a run that completed cleanly still tested nothing.
    nothing_tested_reason: str | None = None
    initialization_lines: list[str | Generator[str, None, None]] = field(default_factory=list)
    summary_lines: list[str | Generator[str, None, None]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.statistic.baseline = self.config.load_baseline()

    def add_initialization_line(self, line: str | Generator[str, None, None]) -> None:
        self.initialization_lines.append(line)

    def add_summary_line(self, line: str | Generator[str, None, None]) -> None:
        self.summary_lines.append(line)

    def summary(self) -> SummaryData:
        """Reduce the run so far into the numbers reports and the terminal share."""
        raise NotImplementedError

    def on_event(self, event: events.EngineEvent) -> None:
        if isinstance(event, LoadingFinished):
            self.find_operation_by_label = event.find_operation_by_label
            self.specification = event.specification
            self.api_statistic = event.statistic

    def check_nothing_tested(self, stop_reason: StopReason) -> None:
        """Turn a clean run that generated no test cases into a configuration error."""
        if (
            self.exit_code != 0
            or stop_reason is not StopReason.COMPLETED
            or self.api_statistic is None
            or self.statistic.total_cases
        ):
            return
        operations = self.api_statistic.operations
        if operations.total == 0:
            self.nothing_tested_reason = "The schema defines no API operations"
        elif operations.selected == 0:
            self.nothing_tested_reason = "No operations matched the filters"
        else:
            self.nothing_tested_reason = self.all_skipped_reason()
        self.exit_code = 2

    def all_skipped_reason(self) -> str:
        """Why every selected operation was skipped."""
        return "Every selected operation was skipped"
