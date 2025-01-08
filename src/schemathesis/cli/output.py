from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from types import GeneratorType
from typing import Any, Generator, Iterable, cast

import click

from schemathesis.cli.cassettes import CassetteConfig
from schemathesis.cli.constants import ISSUE_TRACKER_URL
from schemathesis.core.errors import LoaderError, LoaderErrorKind, format_exception, split_traceback
from schemathesis.core.failures import MessageBlock, Severity, format_failures
from schemathesis.runner import Status
from schemathesis.runner.phases import PhaseName, PhaseSkipReason
from schemathesis.runner.recorder import Interaction
from schemathesis.schemas import ApiOperationsCount

from ..experimental import GLOBAL_EXPERIMENTS
from ..runner import events
from .context import ExecutionContext, GroupedFailures
from .handlers import EventHandler

IO_ENCODING = os.getenv("PYTHONIOENCODING", "utf-8")
DISCORD_LINK = "https://discord.gg/R9ASRAmHnA"


def get_terminal_width() -> int:
    # Some CI/CD providers (e.g. CircleCI) return a (0, 0) terminal size so provide a default
    return shutil.get_terminal_size((80, 24)).columns


def display_section_name(title: str, separator: str = "=", **kwargs: Any) -> None:
    """Print section name with separators in terminal with the given title nicely centered."""
    message = f" {title} ".center(get_terminal_width(), separator)
    kwargs.setdefault("bold", True)
    click.secho(message, **kwargs)


def get_percentage(position: int, length: int) -> str:
    """Format completion percentage in square brackets."""
    percentage_message = f"{position * 100 // length}%".rjust(4)
    return f"[{percentage_message}]"


def bold(option: str) -> str:
    return click.style(option, bold=True)


def display_failures(ctx: ExecutionContext) -> None:
    """Display all failures in the test run."""
    if not ctx.statistic.failures:
        return

    display_section_name("FAILURES")
    for label, failures in ctx.statistic.failures.items():
        display_failures_for_single_test(ctx, label, failures.values())


if IO_ENCODING != "utf-8":

    def _style(text: str, **kwargs: Any) -> str:
        text = text.encode(IO_ENCODING, errors="replace").decode("utf-8")
        return click.style(text, **kwargs)

else:

    def _style(text: str, **kwargs: Any) -> str:
        return click.style(text, **kwargs)


def failure_formatter(block: MessageBlock, content: str) -> str:
    if block == MessageBlock.CASE_ID:
        return _style(content, bold=True)
    if block == MessageBlock.FAILURE:
        return _style(content, fg="red", bold=True)
    if block == MessageBlock.STATUS:
        return _style(content, bold=True)
    assert block == MessageBlock.CURL
    return _style(content.replace("Reproduce with", bold("Reproduce with")))


def display_failures_for_single_test(ctx: ExecutionContext, label: str, checks: Iterable[GroupedFailures]) -> None:
    """Display a failure for a single method / path."""
    display_section_name(label, "_", fg="red")
    for idx, group in enumerate(checks, 1):
        click.echo(
            format_failures(
                case_id=f"{idx}. Test Case ID: {group.case_id}",
                response=group.response,
                failures=group.failures,
                curl=group.code_sample,
                formatter=failure_formatter,
                config=ctx.output_config,
            )
        )
        click.echo()


VERIFY_URL_SUGGESTION = "Verify that the URL points directly to the Open API schema"
DISABLE_SSL_SUGGESTION = f"Bypass SSL verification with {bold('`--request-tls-verify=false`')}."
LOADER_ERROR_SUGGESTIONS = {
    # SSL-specific connection issue
    LoaderErrorKind.CONNECTION_SSL: DISABLE_SSL_SUGGESTION,
    # Other connection problems
    LoaderErrorKind.CONNECTION_OTHER: f"Use {bold('`--wait-for-schema=NUM`')} to wait up to NUM seconds for schema availability.",
    # Response issues
    LoaderErrorKind.UNEXPECTED_CONTENT_TYPE: VERIFY_URL_SUGGESTION,
    LoaderErrorKind.HTTP_FORBIDDEN: "Verify your API keys or authentication headers.",
    LoaderErrorKind.HTTP_NOT_FOUND: VERIFY_URL_SUGGESTION,
    # OpenAPI specification issues
    LoaderErrorKind.OPEN_API_UNSPECIFIED_VERSION: "Include the version in the schema.",
    # YAML specific issues
    LoaderErrorKind.YAML_NUMERIC_STATUS_CODES: "Convert numeric status codes to strings.",
    LoaderErrorKind.YAML_NON_STRING_KEYS: "Convert non-string keys to strings.",
    # Unclassified
    LoaderErrorKind.UNCLASSIFIED: f"If you suspect this is a Schemathesis issue and the schema is valid, please report it and include the schema if you can:\n\n  {ISSUE_TRACKER_URL}",
}


def _display_extras(extras: list[str]) -> None:
    if extras:
        click.echo()
    for extra in extras:
        click.secho(f"    {extra}")


def _maybe_display_tip(suggestion: str | None) -> None:
    # Display suggestion if any
    if suggestion is not None:
        click.secho(f"\n{click.style('Tip:', bold=True, fg='green')} {suggestion}")


DEFAULT_INTERNAL_ERROR_MESSAGE = "An internal error occurred during the test run"
TRUNCATION_PLACEHOLDER = "[...]"


def _print_lines(lines: list[str | Generator[str, None, None]]) -> None:
    for entry in lines:
        if isinstance(entry, str):
            click.echo(entry)
        elif isinstance(entry, GeneratorType):
            for line in entry:
                click.echo(line)


@dataclass
class WarningData:
    missing_auth: dict[int, list[str]] = field(default_factory=dict)


@dataclass
class OutputHandler(EventHandler):
    workers_num: int
    rate_limit: str | None
    wait_for_schema: float | None
    operations_processed: int = 0
    operations_count: ApiOperationsCount | None = None
    skip_reasons: list[str] = field(default_factory=list)
    current_line_length: int = 0
    cassette_config: CassetteConfig | None = None
    junit_xml_file: str | None = None
    warnings: WarningData = field(default_factory=WarningData)
    errors: list[events.NonFatalError] = field(default_factory=list)
    phases: dict[PhaseName, tuple[Status, PhaseSkipReason | None]] = field(
        default_factory=lambda: {phase: (Status.SKIP, None) for phase in PhaseName}
    )

    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, events.Initialized):
            self._on_initialized(ctx, event)
        elif isinstance(event, events.PhaseStarted):
            self._on_phase_started(event)
        elif isinstance(event, events.PhaseFinished):
            self._on_phase_finished(event)
        elif isinstance(event, events.ScenarioStarted):
            self._on_scenario_started(event)
        elif isinstance(event, events.ScenarioFinished):
            self._on_scenario_finished(event)
        if isinstance(event, events.EngineFinished):
            self._on_engine_finished(ctx, event)
        elif isinstance(event, events.Interrupted):
            self._on_interrupted()
        elif isinstance(event, events.FatalError):
            self._on_fatal_error(event)
        elif isinstance(event, events.NonFatalError):
            self.errors.append(event)

    def _on_initialized(self, ctx: ExecutionContext, event: events.Initialized) -> None:
        """Display initialization info, including any lines added by other handlers."""
        self.operations_count = event.operations_count
        display_section_name("Schemathesis test session starts")
        if event.location is not None:
            click.secho(f"Schema location: {event.location}", bold=True)
        click.secho(f"Base URL: {event.base_url}", bold=True)
        click.secho(f"Specification version: {event.specification.name}", bold=True)
        if event.seed is not None:
            click.secho(f"Random seed: {event.seed}", bold=True)
        click.secho(f"Workers: {self.workers_num}", bold=True)
        if self.rate_limit is not None:
            click.secho(f"Rate limit: {self.rate_limit}", bold=True)
        click.secho(f"Collected API operations: {self.operations_count.selected}", bold=True)
        links_count = cast(int, event.links_count)
        click.secho(f"Collected API links: {links_count}", bold=True)
        if ctx.initialization_lines:
            _print_lines(ctx.initialization_lines)

    def _on_phase_started(self, event: events.PhaseStarted) -> None:
        if event.phase.name == PhaseName.PROBING:
            click.secho("API probing: ...\r", bold=True, nl=False)
        elif event.phase.name == PhaseName.STATEFUL_TESTING and event.phase.is_enabled:
            click.secho("Stateful tests\n", bold=True)

    def _on_phase_finished(self, event: events.PhaseFinished) -> None:
        self.phases[event.phase.name] = (event.status, event.phase.skip_reason)
        if event.phase.name == PhaseName.PROBING:
            click.secho(f"API probing: {event.status.name}", bold=True, nl=False)
            click.echo("\n")
        elif event.phase.name == PhaseName.STATEFUL_TESTING and event.phase.is_enabled:
            if event.status != Status.INTERRUPTED:
                click.echo("\n")
        elif event.phase.name == PhaseName.UNIT_TESTING and event.phase.is_enabled:
            if event.status != Status.INTERRUPTED:
                click.echo()
            if self.workers_num > 1:
                click.echo()

    def _on_scenario_started(self, event: events.ScenarioStarted) -> None:
        if event.phase == PhaseName.UNIT_TESTING and self.workers_num == 1:
            # We should display execution result + percentage in the end. For example:
            assert event.label is not None
            max_length = get_terminal_width() - len(" . [XXX%]") - len(TRUNCATION_PLACEHOLDER)
            message = event.label
            message = message[:max_length] + (message[max_length:] and "[...]") + " "
            self.current_line_length = len(message)
            click.echo(message, nl=False)

    def _on_scenario_finished(self, event: events.ScenarioFinished) -> None:
        self.operations_processed += 1
        if event.phase == PhaseName.UNIT_TESTING:
            if event.status == Status.SKIP and event.skip_reason is not None:
                self.skip_reasons.append(event.skip_reason)
            self._display_execution_result(event.status)
            self._check_warnings(event)
            if self.workers_num == 1:
                self.display_percentage()
        elif (
            event.phase == PhaseName.STATEFUL_TESTING
            and not event.is_final
            and event.status != Status.INTERRUPTED
            and event.status is not None
        ):
            self._display_execution_result(event.status)

    def _check_warnings(self, event: events.ScenarioFinished) -> None:
        for status_code in (401, 403):
            if has_too_many_responses_with_status(event.recorder.interactions.values(), status_code):
                self.warnings.missing_auth.setdefault(status_code, []).append(event.recorder.label)

    def _display_execution_result(self, status: Status) -> None:
        """Display an appropriate symbol for the given event's execution result."""
        symbol, color = {
            Status.SUCCESS: (".", "green"),
            Status.FAILURE: ("F", "red"),
            Status.ERROR: ("E", "red"),
            Status.SKIP: ("S", "yellow"),
            Status.INTERRUPTED: ("S", "yellow"),
        }[status]
        self.current_line_length += len(symbol)
        click.secho(symbol, nl=False, fg=color)

    def _on_interrupted(self) -> None:
        click.echo()
        display_section_name("KeyboardInterrupt", "!", bold=False)
        click.echo()

    def _on_fatal_error(self, event: events.FatalError) -> None:
        if isinstance(event.exception, LoaderError):
            title = "Schema Loading Error"
            message = event.exception.message
            extras = event.exception.extras
            suggestion = LOADER_ERROR_SUGGESTIONS.get(event.exception.kind)
        else:
            title = "Test Execution Error"
            message = DEFAULT_INTERNAL_ERROR_MESSAGE
            traceback = format_exception(event.exception, with_traceback=True)
            extras = split_traceback(traceback)
            suggestion = (
                f"Please consider reporting the traceback above to our issue tracker:\n\n  {ISSUE_TRACKER_URL}."
            )
        click.secho(title, fg="red", bold=True)
        click.echo()
        click.secho(message)
        _display_extras(extras)
        if not (
            isinstance(event.exception, LoaderError)
            and event.exception.kind == LoaderErrorKind.CONNECTION_OTHER
            and self.wait_for_schema is not None
        ):
            _maybe_display_tip(suggestion)

        raise click.Abort

    def display_warnings(self) -> None:
        display_section_name("WARNINGS")
        total = sum(len(endpoints) for endpoints in self.warnings.missing_auth.values())
        suffix = "" if total == 1 else "s"
        click.secho(
            f"\nMissing or invalid API credentials: {total} API operation{suffix} returned authentication errors\n",
            fg="yellow",
        )

        for status_code, operations in self.warnings.missing_auth.items():
            status_text = "Unauthorized" if status_code == 401 else "Forbidden"
            count = len(operations)
            suffix = "" if count == 1 else "s"
            click.secho(
                f"{status_code} {status_text} ({count} operation{suffix}):",
                fg="yellow",
            )
            # Show first few API operations
            for endpoint in operations[:3]:
                click.secho(f"  • {endpoint}", fg="yellow")
            if len(operations) > 3:
                click.secho(f"  + {len(operations) - 3} more", fg="yellow")
            click.echo()
        click.secho("Tip: ", bold=True, fg="yellow", nl=False)
        click.secho(f"Use {bold('--auth')} ", fg="yellow", nl=False)
        click.secho(f"or {bold('-H')} ", fg="yellow", nl=False)
        click.secho("to provide authentication credentials", fg="yellow")
        click.echo()

    def display_experiments(self) -> None:
        display_section_name("EXPERIMENTS")

        click.echo()
        for experiment in sorted(GLOBAL_EXPERIMENTS.enabled, key=lambda e: e.name):
            click.secho(f"🧪 {experiment.name}: ", bold=True, nl=False)
            click.secho(experiment.description)
            click.secho(f"   Feedback: {experiment.discussion_url}")
            click.echo()

        click.secho(
            "Your feedback is crucial for experimental features. "
            "Please visit the provided URL(s) to share your thoughts.",
            dim=True,
        )
        click.echo()

    def display_api_operations(self, ctx: ExecutionContext) -> None:
        assert self.operations_count is not None
        click.secho("API Operations:", bold=True)
        click.secho(
            f"  Selected: {click.style(str(self.operations_count.selected), bold=True)}/"
            f"{click.style(str(self.operations_count.total), bold=True)}"
        )
        click.secho(f"  Tested: {click.style(str(len(ctx.statistic.tested_operations)), bold=True)}")
        errors = len(
            {
                err.label
                for err in self.errors
                # Some API operations may have some tests before they have an error
                if err.phase == PhaseName.UNIT_TESTING
                and err.label not in ctx.statistic.tested_operations
                and err.related_to_operation
            }
        )
        if errors:
            click.secho(f"  Errored: {click.style(str(errors), bold=True)}")

        # API operations that are skipped due to fail-fast are counted here as well
        total_skips = self.operations_count.selected - len(ctx.statistic.tested_operations) - errors
        if total_skips:
            click.secho(f"  Skipped: {click.style(str(total_skips), bold=True)}")
            for reason in sorted(set(self.skip_reasons)):
                click.secho(f"    - {reason.rstrip('.')}")
        click.echo()

    def display_phases(self) -> None:
        click.secho("Test Phases:", bold=True)

        for phase in PhaseName:
            status, skip_reason = self.phases[phase]

            if status == Status.SKIP:
                click.secho(f"  ⏭️ {phase.value}", fg="yellow", nl=False)
                if skip_reason:
                    click.secho(f" ({skip_reason.value})", fg="yellow")
                else:
                    click.echo()
            elif status == Status.SUCCESS:
                click.secho(f"  ✅ {phase.value}", fg="green")
            elif status == Status.FAILURE:
                click.secho(f"  ❌ {phase.value}", fg="red")
            elif status == Status.ERROR:
                click.secho(f"  🚫 {phase.value}", fg="red")
            elif status == Status.INTERRUPTED:
                click.secho(f"  ⚡ {phase.value}", fg="yellow")
        click.echo()

    def display_test_cases(self, ctx: ExecutionContext) -> None:
        if ctx.statistic.total_cases == 0:
            click.secho("Test cases:", bold=True)
            click.secho("  No test cases were generated\n")
            return

        unique_failures = sum(
            len(group.failures) for grouped in ctx.statistic.failures.values() for group in grouped.values()
        )
        click.secho("Test cases:", bold=True)

        parts = [f"  {click.style(str(ctx.statistic.total_cases), bold=True)} generated"]

        # Don't show pass/fail status if all cases were skipped
        if ctx.statistic.cases_without_checks == ctx.statistic.total_cases:
            parts.append(f"{click.style(str(ctx.statistic.cases_without_checks), bold=True)} skipped")
        else:
            if unique_failures > 0:
                parts.append(
                    f"{click.style(str(ctx.statistic.cases_with_failures), bold=True)} found "
                    f"{click.style(str(unique_failures), bold=True)} unique failures"
                )
            else:
                parts.append(f"{click.style(str(ctx.statistic.total_cases), bold=True)} passed")

            if ctx.statistic.cases_without_checks > 0:
                parts.append(f"{click.style(str(ctx.statistic.cases_without_checks), bold=True)} skipped")

        click.secho(", ".join(parts) + "\n")

    def display_failures_summary(self, ctx: ExecutionContext) -> None:
        # Collect all unique failures and their counts by title
        failure_counts: dict[str, tuple[Severity, int]] = {}
        for grouped in ctx.statistic.failures.values():
            for group in grouped.values():
                for failure in group.failures:
                    data = failure_counts.get(failure.title, (failure.severity, 0))
                    failure_counts[failure.title] = (failure.severity, data[1] + 1)

        click.secho("Failures:", bold=True)

        # Sort by severity first, then by title
        sorted_failures = sorted(failure_counts.items(), key=lambda x: (x[1][0], x[0]))

        for title, (_, count) in sorted_failures:
            click.secho(f"  ❌ {title}: ", nl=False)
            click.secho(str(count), bold=True)
        click.echo()

    def display_errors_summary(self) -> None:
        # Group errors by title and count occurrences
        error_counts: dict[str, int] = {}
        for error in self.errors:
            title = error.info.title
            error_counts[title] = error_counts.get(title, 0) + 1

        click.secho("Errors:", bold=True)

        for title in sorted(error_counts):
            click.secho(f"  🚫 {title}: ", nl=False)
            click.secho(str(error_counts[title]), bold=True)
        click.echo()

    def display_final_line(self, ctx: ExecutionContext, event: events.EngineFinished) -> None:
        parts = []

        unique_failures = sum(
            len(group.failures) for grouped in ctx.statistic.failures.values() for group in grouped.values()
        )
        if unique_failures:
            parts.append(f"{unique_failures} failures")

        if self.errors:
            parts.append(f"{len(self.errors)} errors")

        total_warnings = sum(len(endpoints) for endpoints in self.warnings.missing_auth.values())
        if total_warnings:
            parts.append(f"{total_warnings} warnings")

        if parts:
            message = f'{", ".join(parts)} in {event.running_time:.2f}s'
            color = "red" if (unique_failures or self.errors) else "yellow"
        elif ctx.statistic.total_cases == 0:
            message = "Empty test suite"
            color = "yellow"
        else:
            message = f"No issues found in {event.running_time:.2f}s"
            color = "green"

        display_section_name(message, fg=color)

    def display_reports(self) -> None:
        reports = []
        if self.cassette_config is not None:
            format_name = self.cassette_config.format.name.upper()
            reports.append((format_name, self.cassette_config.path.name))
        if self.junit_xml_file is not None:
            reports.append(("JUnit XML", self.junit_xml_file))

        if reports:
            click.secho("Reports:", bold=True)
            for report_type, path in reports:
                click.secho(f"  • {report_type}: {path}")
            click.echo()

    def _on_engine_finished(self, ctx: ExecutionContext, event: events.EngineFinished) -> None:
        if self.errors:
            display_section_name("ERRORS")
            errors = sorted(self.errors, key=lambda r: (r.phase.value, r.label))
            for error in errors:
                display_section_name(error.label, "_", fg="red")
                click.echo(error.info.format(bold=lambda x: click.style(x, bold=True)))
            click.secho(
                f"\nNeed more help?\n    Join our Discord server: {DISCORD_LINK}",
                fg="red",
            )
        display_failures(ctx)
        if self.warnings.missing_auth:
            self.display_warnings()
        if GLOBAL_EXPERIMENTS.enabled:
            self.display_experiments()
        display_section_name("SUMMARY")
        click.echo()

        if self.operations_count:
            self.display_api_operations(ctx)

        self.display_phases()

        if ctx.statistic.failures:
            self.display_failures_summary(ctx)

        if self.errors:
            self.display_errors_summary()

        if self.warnings.missing_auth:
            affected = sum(len(operations) for operations in self.warnings.missing_auth.values())
            click.secho("Warnings:", bold=True)
            click.secho(f"  ⚠️ Missing authentication: {bold(str(affected))}", fg="yellow")
            click.echo()

        if ctx.summary_lines:
            _print_lines(ctx.summary_lines)
            click.echo()

        self.display_test_cases(ctx)
        self.display_reports()
        self.display_final_line(ctx, event)

    def display_percentage(self) -> None:
        """Add the current progress in % to the right side of the current line."""
        assert self.operations_count is not None
        selected = self.operations_count.selected
        current_percentage = get_percentage(self.operations_processed, selected)
        styled = click.style(current_percentage, fg="cyan")
        # Total length of the message, so it will fill to the right border of the terminal.
        # Padding is already taken into account in `ctx.current_line_length`
        length = max(get_terminal_width() - self.current_line_length + len(styled) - len(current_percentage), 1)
        template = f"{{:>{length}}}"
        click.echo(template.format(styled))


TOO_MANY_RESPONSES_WARNING_TEMPLATE = (
    "Most of the responses from {} have a {} status code. Did you specify proper API credentials?"
)
TOO_MANY_RESPONSES_THRESHOLD = 0.9


def has_too_many_responses_with_status(interactions: Iterable[Interaction], status_code: int) -> bool:
    matched = 0
    total = 0
    for interaction in interactions:
        if interaction.response is not None:
            if interaction.response.status_code == status_code:
                matched += 1
            total += 1
    if not total:
        return False
    return matched / total >= TOO_MANY_RESPONSES_THRESHOLD
