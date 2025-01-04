from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from types import GeneratorType
from typing import Any, Generator, cast

import click

from schemathesis.cli.constants import ISSUE_TRACKER_URL
from schemathesis.core import SCHEMATHESIS_TEST_CASE_HEADER
from schemathesis.core.errors import LoaderError, LoaderErrorKind, format_exception, split_traceback
from schemathesis.core.failures import MessageBlock, format_failures
from schemathesis.runner import Status
from schemathesis.runner.phases import PhaseName

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


def display_summary(ctx: ExecutionContext, event: events.EngineFinished) -> None:
    message, color = get_summary_output(ctx, event)
    display_section_name(message, fg=color)


def get_summary_message_parts(ctx: ExecutionContext) -> list[str]:
    parts = []
    passed = ctx.statistic.outcomes.get(Status.SUCCESS)
    if passed:
        parts.append(f"{passed} passed")
    failed = ctx.statistic.outcomes.get(Status.FAILURE)
    if failed:
        parts.append(f"{failed} failed")
    errored = len(ctx.errors)
    if errored:
        parts.append(f"{errored} errored")
    skipped = ctx.statistic.outcomes.get(Status.SKIP)
    if skipped:
        parts.append(f"{skipped} skipped")
    return parts


def get_summary_output(ctx: ExecutionContext, event: events.EngineFinished) -> tuple[str, str]:
    parts = get_summary_message_parts(ctx)
    if not parts:
        message = "Empty test suite"
        color = "yellow"
    else:
        message = f'{", ".join(parts)} in {event.running_time:.2f}s'
        if Status.FAILURE in ctx.statistic.outcomes or Status.ERROR in ctx.statistic.outcomes:
            color = "red"
        elif Status.SKIP in ctx.statistic.outcomes:
            color = "yellow"
        else:
            color = "green"
    return message, color


def display_errors(ctx: ExecutionContext) -> None:
    """Display all errors in the test run."""
    if not ctx.errors:
        return

    display_section_name("ERRORS")
    errors = sorted(ctx.errors, key=lambda r: (r.phase.value, r.label))
    for error in errors:
        display_section_name(error.label, "_", fg="red")
        click.echo(error.info.format(bold=lambda x: click.style(x, bold=True)))
    click.secho(
        f"\nNeed more help?\n    Join our Discord server: {DISCORD_LINK}",
        fg="red",
    )


def bold(option: str) -> str:
    return click.style(option, bold=True)


def display_failures(ctx: ExecutionContext) -> None:
    """Display all failures in the test run."""
    if not ctx.statistic.failures:
        return

    display_section_name("FAILURES")
    for label, failures in ctx.statistic.failures.items():
        display_failures_for_single_test(ctx, label, failures)


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


def display_failures_for_single_test(ctx: ExecutionContext, label: str, checks: list[GroupedFailures]) -> None:
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


def display_checks_statistics(total: dict[str, dict[str | Status, int]]) -> None:
    padding = 20
    col1_len = max(map(len, total.keys())) + padding
    col2_len = len(str(max(total.values(), key=lambda v: v["total"])["total"])) * 2 + padding
    col3_len = padding
    click.secho("Performed checks:", bold=True)
    template = f"    {{:{col1_len}}}{{:{col2_len}}}{{:{col3_len}}}"
    for check_name, results in total.items():
        display_check_result(check_name, results, template)


def display_check_result(check_name: str, results: dict[str | Status, int], template: str) -> None:
    """Show results of single check execution."""
    if Status.FAILURE in results:
        verdict = "FAILED"
        color = "red"
    else:
        verdict = "PASSED"
        color = "green"
    success = results.get(Status.SUCCESS, 0)
    total = results.get("total", 0)
    click.echo(template.format(check_name, f"{success} / {total} passed", click.style(verdict, fg=color, bold=True)))


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
class OutputHandler(EventHandler):
    workers_num: int
    rate_limit: str | None
    wait_for_schema: float | None
    operations_processed: int = 0
    operations_count: int | None = None
    seed: int | None = None
    current_line_length: int = 0
    cassette_path: str | None = None
    junit_xml_file: str | None = None
    warnings: list[str] = field(default_factory=list)

    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, events.Initialized):
            self._on_initialized(ctx, event)
        elif isinstance(event, events.PhaseStarted):
            self._on_phase_started(event)
        elif isinstance(event, events.PhaseFinished):
            self._on_phase_finished(ctx, event)
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
        elif isinstance(event, events.Warning):
            self.warnings.append(event.message)

    def _on_initialized(self, ctx: ExecutionContext, event: events.Initialized) -> None:
        """Display initialization info, including any lines added by other handlers."""
        self.operations_count = cast(int, event.operations_count)  # INVARIANT: should not be `None`
        self.seed = event.seed
        display_section_name("Schemathesis test session starts")
        if event.location is not None:
            click.secho(f"Schema location: {event.location}", bold=True)
        click.secho(f"Base URL: {event.base_url}", bold=True)
        click.secho(f"Specification version: {event.specification.name}", bold=True)
        if self.seed is not None:
            click.secho(f"Random seed: {self.seed}", bold=True)
        click.secho(f"Workers: {self.workers_num}", bold=True)
        if self.rate_limit is not None:
            click.secho(f"Rate limit: {self.rate_limit}", bold=True)
        click.secho(f"Collected API operations: {self.operations_count}", bold=True)
        links_count = cast(int, event.links_count)
        click.secho(f"Collected API links: {links_count}", bold=True)
        if ctx.initialization_lines:
            _print_lines(ctx.initialization_lines)

    def _on_phase_started(self, event: events.PhaseStarted) -> None:
        if event.phase.name == PhaseName.PROBING:
            click.secho("API probing: ...\r", bold=True, nl=False)
        elif event.phase.name == PhaseName.STATEFUL_TESTING and event.phase.is_enabled:
            click.secho("Stateful tests\n", bold=True)

    def _on_phase_finished(self, ctx: ExecutionContext, event: events.PhaseFinished) -> None:
        if event.phase.name == PhaseName.PROBING:
            click.secho(f"API probing: {event.status.name}", bold=True, nl=False)
            click.echo("\n")
        elif event.phase.name == PhaseName.STATEFUL_TESTING and event.phase.is_enabled:
            if event.payload is None:
                return

            # Merge execution data from sink into the complete transition table
            sink = ctx.state_machine_sink
            assert sink is not None
            transitions = sink.transitions.transitions  # type: ignore[attr-defined]

            for source, status_codes in event.payload.transitions.items():
                # Ensure source exists in sink
                sink_source = transitions.setdefault(source, {})
                for status_code, targets in status_codes.items():
                    # Ensure status_code exists in sink
                    sink_status = sink_source.setdefault(status_code, {})
                    for target, links in targets.items():
                        # Ensure target exists in sink
                        sink_target = sink_status.setdefault(target, {})
                        for link_name, counters in links.items():
                            # If sink doesn't have this link, add it with zero counters
                            if link_name not in sink_target:
                                sink_target[link_name] = counters
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
            self._display_execution_result(event.status)
            if self.workers_num == 1:
                self.display_percentage()
        elif (
            event.phase == PhaseName.STATEFUL_TESTING
            and not event.is_final
            and event.status != Status.INTERRUPTED
            and event.status is not None
        ):
            self._display_execution_result(event.status)

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

    def _on_engine_finished(self, ctx: ExecutionContext, event: events.EngineFinished) -> None:
        display_errors(ctx)
        display_failures(ctx)
        display_section_name("SUMMARY")
        click.echo()
        total = {key: dict(value) for key, value in ctx.statistic.totals.items()}

        if ctx.state_machine_sink is not None:
            click.echo(ctx.state_machine_sink.transitions.to_formatted_table(get_terminal_width()))
            click.echo()
        if not ctx.statistic.outcomes or not total:
            click.secho("No checks were performed.", bold=True)

        if total:
            display_checks_statistics(total)

        if self.cassette_path:
            click.echo()
            category = click.style("Network log", bold=True)
            click.secho(f"{category}: {self.cassette_path}")

        if self.junit_xml_file:
            click.echo()
            category = click.style("JUnit XML file", bold=True)
            click.secho(f"{category}: {self.junit_xml_file}")

        if self.warnings:
            click.secho("\nWARNINGS:", bold=True, fg="yellow")
            for warning in self.warnings:
                click.secho(f"  - {warning}", fg="yellow")

        if len(GLOBAL_EXPERIMENTS.enabled) > 0:
            click.secho("\nExperimental Features:", bold=True)
            for experiment in sorted(GLOBAL_EXPERIMENTS.enabled, key=lambda e: e.name):
                click.secho(f"  - {experiment.name}: {experiment.description}")
                click.secho(f"    Feedback: {experiment.discussion_url}")
            click.echo()
            click.echo(
                "Your feedback is crucial for experimental features. "
                "Please visit the provided URL(s) to share your thoughts."
            )

        if Status.FAILURE in ctx.statistic.outcomes:
            click.echo(
                f"\n{bold('Note')}: Use the '{SCHEMATHESIS_TEST_CASE_HEADER}' header to correlate test case ids "
                "from failure messages with server logs for debugging."
            )
            if self.seed is not None:
                seed_option = f"`--hypothesis-seed={self.seed}`"
                click.secho(f"\n{bold('Note')}: To replicate these test failures, rerun with {bold(seed_option)}")

        if ctx.summary_lines:
            click.echo()
            _print_lines(ctx.summary_lines)
        click.echo()
        display_summary(ctx, event)

    def display_percentage(self) -> None:
        """Add the current progress in % to the right side of the current line."""
        operations_count = cast(int, self.operations_count)  # is already initialized via `Initialized` event
        current_percentage = get_percentage(self.operations_processed, operations_count)
        styled = click.style(current_percentage, fg="cyan")
        # Total length of the message, so it will fill to the right border of the terminal.
        # Padding is already taken into account in `ctx.current_line_length`
        length = max(get_terminal_width() - self.current_line_length + len(styled) - len(current_percentage), 1)
        template = f"{{:>{length}}}"
        click.echo(template.format(styled))
