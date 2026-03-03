"""Output handler for the fuzz command."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

import click

from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.cli.commands.fuzz.continuous_fuzzing import ContinuousFuzzingProgressManager
from schemathesis.cli.commands.run.handlers.output import BaseOutputHandler
from schemathesis.cli.output import BLOCK_PADDING, _print_lines, _style, display_section_name
from schemathesis.engine import Status, events
from schemathesis.engine.phases import PhaseName


@dataclass
class FuzzOutputHandler(BaseOutputHandler):
    """Output handler for ``st fuzz``.

    Shares loading, error, failure, and warning display with ``BaseOutputHandler``
    but replaces the per-operation unit-test progress with a continuous fuzzing
    progress manager, and swaps the SUMMARY section to show fuzzing statistics
    instead of the phase table and test-case counts.
    """

    continuous_fuzzing_manager: ContinuousFuzzingProgressManager | None = None

    def _on_non_fatal_error(self, event: events.NonFatalError) -> None:
        super()._on_non_fatal_error(event)
        if self.continuous_fuzzing_manager is not None:
            self.continuous_fuzzing_manager.update_non_fatal_errors(len(self.errors))

    def shutdown(self, ctx: ExecutionContext) -> None:
        super().shutdown(ctx)
        if self.continuous_fuzzing_manager is not None:
            self.continuous_fuzzing_manager.stop()

    def _on_phase_started(self, event: events.PhaseStarted) -> None:
        phase = event.phase
        if phase.name == PhaseName.FUZZING and phase.is_enabled:
            self._start_continuous_fuzzing()

    def _start_continuous_fuzzing(self) -> None:
        assert self.statistic is not None
        assert self.continuous_fuzzing_manager is None
        self.continuous_fuzzing_manager = ContinuousFuzzingProgressManager(
            console=self.console,
            title=PhaseName.FUZZING.value,
            total_operations=self.statistic.operations.selected,
        )
        self.continuous_fuzzing_manager.start()

    def _on_phase_finished(self, event: events.PhaseFinished) -> None:
        phase = event.phase
        if phase.name == PhaseName.FUZZING and phase.is_enabled and self.continuous_fuzzing_manager is not None:
            from rich.padding import Padding
            from rich.text import Text

            self.continuous_fuzzing_manager.stop()
            if event.status == Status.ERROR:
                message = self.continuous_fuzzing_manager.get_completion_message("🚫")
            else:
                message = self.continuous_fuzzing_manager.get_completion_message()
            self.console.print(Padding(Text(message, style="white"), BLOCK_PADDING))
            self.console.print()
            self.continuous_fuzzing_manager = None

    def _on_scenario_finished(self, ctx: ExecutionContext, event: events.ScenarioFinished) -> None:
        if event.phase == PhaseName.FUZZING and self.continuous_fuzzing_manager is not None:
            self.continuous_fuzzing_manager.update_stats(
                event.status,
                label=event.label,
                unique_failures=len(ctx.statistic.unique_failures_map),
                non_fatal_errors=len(self.errors),
            )
            self._check_warnings(ctx, event)

    def _on_interrupted(self, event: events.Interrupted) -> None:
        if self.continuous_fuzzing_manager is not None:
            self.continuous_fuzzing_manager.interrupt()
        else:
            super()._on_interrupted(event)

    def _on_engine_finished(self, ctx: ExecutionContext, event: events.EngineFinished) -> None:
        assert self.loading_manager is None
        assert self.continuous_fuzzing_manager is None
        self._display_errors()
        self._display_failures(ctx)
        if not self.warnings.is_empty:
            self.display_warnings()
        display_section_name("SUMMARY")
        click.echo()

        if self.statistic:
            self.display_api_operations(ctx)

        stop_reason, remaining_summary_lines = self._extract_fuzz_stop_reason(ctx.summary_lines)
        self.display_fuzzing_statistics(ctx, event, stop_reason=stop_reason)
        if remaining_summary_lines:
            _print_lines(remaining_summary_lines)
            click.echo()

        if ctx.statistic.failures:
            self.display_failures_summary(ctx)

        if self.errors:
            self.display_errors_summary()

        if not self.warnings.is_empty:
            self._display_warnings_summary()

        self.display_reports()
        self.display_seed()
        self.display_final_line(ctx, event)

    def display_fuzzing_statistics(
        self, ctx: ExecutionContext, event: events.EngineFinished, *, stop_reason: str | None = None
    ) -> None:
        if event.running_time < 1.0:
            duration = "<1s"
            throughput = "n/a (run too short)"
        else:
            duration = f"{event.running_time:.2f}s"
            throughput_value = ctx.statistic.total_cases / event.running_time
            throughput = f"{throughput_value:.1f} cases/s"

        click.echo(_style("Fuzzing:", bold=True))
        if stop_reason is not None:
            click.echo(_style(f"  Stopped: {click.style(stop_reason, bold=True)}"))
        click.echo(_style(f"  Duration: {click.style(duration, bold=True)}"))
        click.echo(_style(f"  Test cases: {click.style(str(ctx.statistic.total_cases), bold=True)}"))
        click.echo(_style(f"  Avg throughput: {click.style(throughput, bold=True)}"))
        click.echo()

    def _extract_fuzz_stop_reason(
        self, lines: list[str | Generator[str, None, None]]
    ) -> tuple[str | None, list[str | Generator[str, None, None]]]:
        stop_reason: str | None = None
        remaining: list[str | Generator[str, None, None]] = []
        for entry in lines:
            if isinstance(entry, str) and stop_reason is None and entry.startswith("Fuzzing stopped:"):
                value = entry.removeprefix("Fuzzing stopped:").strip()
                stop_reason = value.rstrip(".")
            else:
                remaining.append(entry)
        return stop_reason, remaining
