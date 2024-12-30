from __future__ import annotations

import os
import shutil
from collections import Counter
from types import GeneratorType
from typing import TYPE_CHECKING, Any, Generator, cast

import click

from schemathesis.cli.constants import ISSUE_TRACKER_URL
from schemathesis.core import SCHEMATHESIS_TEST_CASE_HEADER
from schemathesis.core.errors import LoaderError, LoaderErrorKind, format_exception, split_traceback
from schemathesis.core.failures import MessageBlock, format_failures
from schemathesis.runner import Status
from schemathesis.runner.models import group_failures_by_code_sample
from schemathesis.runner.models.check import Check

from ... import experimental
from ...experimental import GLOBAL_EXPERIMENTS
from ...runner import events
from ...stateful.sink import StateMachineSink
from ..context import ExecutionContext
from ..handlers import EventHandler

if TYPE_CHECKING:
    from schemathesis.runner.phases.stateful import StatefulTestingPayload

SPINNER_REPETITION_NUMBER = 10
IO_ENCODING = os.getenv("PYTHONIOENCODING", "utf-8")
DISCORD_LINK = "https://discord.gg/R9ASRAmHnA"
GITHUB_APP_LINK = "https://github.com/apps/schemathesis"


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


def display_execution_result(ctx: ExecutionContext, status: Status) -> None:
    """Display an appropriate symbol for the given event's execution result."""
    symbol, color = {
        Status.SUCCESS: (".", "green"),
        Status.FAILURE: ("F", "red"),
        Status.ERROR: ("E", "red"),
        Status.SKIP: ("S", "yellow"),
        Status.INTERRUPTED: ("S", "yellow"),
    }[status]
    ctx.current_line_length += len(symbol)
    click.secho(symbol, nl=False, fg=color)


def display_percentage(ctx: ExecutionContext, event: events.AfterExecution) -> None:
    """Add the current progress in % to the right side of the current line."""
    operations_count = cast(int, ctx.operations_count)  # is already initialized via `Initialized` event
    current_percentage = get_percentage(ctx.operations_processed, operations_count)
    styled = click.style(current_percentage, fg="cyan")
    # Total length of the message, so it will fill to the right border of the terminal.
    # Padding is already taken into account in `ctx.current_line_length`
    length = max(get_terminal_width() - ctx.current_line_length + len(styled) - len(current_percentage), 1)
    template = f"{{:>{length}}}"
    click.echo(template.format(styled))


def display_summary(ctx: ExecutionContext, event: events.EngineFinished) -> None:
    message, color = get_summary_output(ctx, event)
    display_section_name(message, fg=color)


def get_summary_message_parts(ctx: ExecutionContext, event: events.EngineFinished) -> list[str]:
    parts = []
    passed = event.outcome_statistic.get(Status.SUCCESS)
    if passed:
        parts.append(f"{passed} passed")
    failed = event.outcome_statistic.get(Status.FAILURE)
    if failed:
        parts.append(f"{failed} failed")
    errored = len(ctx.errors)
    if errored:
        parts.append(f"{errored} errored")
    skipped = event.outcome_statistic.get(Status.SKIP)
    if skipped:
        parts.append(f"{skipped} skipped")
    return parts


def get_summary_output(ctx: ExecutionContext, event: events.EngineFinished) -> tuple[str, str]:
    parts = get_summary_message_parts(ctx, event)
    if not parts:
        message = "Empty test suite"
        color = "yellow"
    else:
        message = f'{", ".join(parts)} in {event.running_time:.2f}s'
        if Status.FAILURE in event.outcome_statistic or Status.ERROR in event.outcome_statistic:
            color = "red"
        elif Status.SKIP in event.outcome_statistic:
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
        f"\nNeed more help?\n" f"    Join our Discord server: {DISCORD_LINK}",
        fg="red",
    )


def bold(option: str) -> str:
    return click.style(option, bold=True)


DISABLE_SSL_SUGGESTION = f"Bypass SSL verification with {bold('`--request-tls-verify=false`')}."


def display_failures(ctx: ExecutionContext, event: events.EngineFinished) -> None:
    """Display all failures in the test run."""
    if Status.FAILURE not in event.outcome_statistic:
        return
    display_section_name("FAILURES")
    for result in ctx.results:
        if not any(check.status == Status.FAILURE for check in result.checks):
            continue
        display_failures_for_single_test(ctx, result.label, result.checks)


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


def display_failures_for_single_test(ctx: ExecutionContext, label: str, checks: list[Check]) -> None:
    """Display a failure for a single method / path."""
    display_section_name(label, "_", fg="red")
    for idx, (code_sample, group) in enumerate(group_failures_by_code_sample(checks), 1):
        # Make server errors appear first in the list of checks
        checks = sorted(group, key=lambda c: c.name != "not_a_server_error")

        check = checks[0]

        click.echo(
            format_failures(
                case_id=f"{idx}. Test Case ID: {check.case.id}",
                response=check.response,
                failures=[check.failure for check in checks if check.failure is not None],
                curl=code_sample,
                formatter=failure_formatter,
                config=ctx.output_config,
            )
        )
        click.echo()


def display_statistic(ctx: ExecutionContext, event: events.EngineFinished) -> None:
    display_section_name("SUMMARY")
    click.echo()
    output: dict[str, dict[str | Status, int]] = {}
    for item in event.results:
        for check in item.checks:
            output.setdefault(check.name, Counter())
            output[check.name][check.status] += 1
            output[check.name]["total"] += 1
    total = {key: dict(value) for key, value in output.items()}

    if ctx.state_machine_sink is not None:
        click.echo(ctx.state_machine_sink.transitions.to_formatted_table(get_terminal_width()))
        click.echo()
    if not event.outcome_statistic or not total:
        click.secho("No checks were performed.", bold=True)

    if total:
        display_checks_statistics(total)

    if ctx.cassette_path:
        click.echo()
        category = click.style("Network log", bold=True)
        click.secho(f"{category}: {ctx.cassette_path}")

    if ctx.junit_xml_file:
        click.echo()
        category = click.style("JUnit XML file", bold=True)
        click.secho(f"{category}: {ctx.junit_xml_file}")

    if ctx.warnings:
        click.secho("\nWARNINGS:", bold=True, fg="yellow")
        for warning in ctx.warnings:
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

    if Status.FAILURE in event.outcome_statistic:
        click.echo(
            f"\n{bold('Note')}: Use the '{SCHEMATHESIS_TEST_CASE_HEADER}' header to correlate test case ids "
            "from failure messages with server logs for debugging."
        )
        if ctx.seed is not None:
            seed_option = f"`--hypothesis-seed={ctx.seed}`"
            click.secho(f"\n{bold('Note')}: To replicate these test failures, rerun with {bold(seed_option)}")


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


def should_skip_suggestion(ctx: ExecutionContext, event: events.FatalError) -> bool:
    return (
        isinstance(event.exception, LoaderError)
        and event.exception.kind == LoaderErrorKind.CONNECTION_OTHER
        and ctx.wait_for_schema is not None
    )


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


def display_internal_error(ctx: ExecutionContext, event: events.FatalError) -> None:
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
        suggestion = f"Please consider reporting the traceback above to our issue tracker:\n\n  {ISSUE_TRACKER_URL}."
    click.secho(title, fg="red", bold=True)
    click.echo()
    click.secho(message)
    _display_extras(extras)
    if not should_skip_suggestion(ctx, event):
        _maybe_display_tip(suggestion)


def on_initialized(ctx: ExecutionContext, event: events.Initialized) -> None:
    """Display information about the test session."""
    ctx.operations_count = cast(int, event.operations_count)  # INVARIANT: should not be `None`
    ctx.seed = event.seed
    display_section_name("Schemathesis test session starts")
    if event.location is not None:
        click.secho(f"Schema location: {event.location}", bold=True)
    click.secho(f"Base URL: {event.base_url}", bold=True)
    click.secho(f"Specification version: {event.specification.name}", bold=True)
    if ctx.seed is not None:
        click.secho(f"Random seed: {ctx.seed}", bold=True)
    click.secho(f"Workers: {ctx.workers_num}", bold=True)
    if ctx.rate_limit is not None:
        click.secho(f"Rate limit: {ctx.rate_limit}", bold=True)
    click.secho(f"Collected API operations: {ctx.operations_count}", bold=True)
    links_count = cast(int, event.links_count)
    click.secho(f"Collected API links: {links_count}", bold=True)
    if ctx.initialization_lines:
        _print_lines(ctx.initialization_lines)


def on_probing_started() -> None:
    click.secho("API probing: ...\r", bold=True, nl=False)


def on_probing_finished(ctx: ExecutionContext, status: Status) -> None:
    click.secho(f"API probing: {status.name}\n\n", bold=True, nl=False)


def on_stateful_testing_started(ctx: ExecutionContext) -> None:
    from schemathesis.specs.openapi.stateful.statistic import OpenAPILinkStats

    if not experimental.STATEFUL_ONLY.is_enabled:
        click.echo()
    ctx.state_machine_sink = StateMachineSink(transitions=OpenAPILinkStats())
    click.secho("Stateful tests\n", bold=True)


def on_stateful_testing_finished(ctx: ExecutionContext, payload: StatefulTestingPayload | None) -> None:
    if payload is None:
        return
    ctx.results.append(payload.result)

    # Merge execution data from sink into the complete transition table
    sink = ctx.state_machine_sink
    assert sink is not None
    transitions = sink.transitions.transitions  # type: ignore[attr-defined]

    for source, status_codes in payload.transitions.items():
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


TRUNCATION_PLACEHOLDER = "[...]"


def on_before_execution(ctx: ExecutionContext, event: events.BeforeExecution) -> None:
    """Display what method / path will be tested next."""
    # We should display execution result + percentage in the end. For example:
    max_length = get_terminal_width() - len(" . [XXX%]") - len(TRUNCATION_PLACEHOLDER)
    message = event.label
    message = message[:max_length] + (message[max_length:] and "[...]") + " "
    ctx.current_line_length = len(message)
    click.echo(message, nl=False)


def on_after_execution(ctx: ExecutionContext, event: events.AfterExecution) -> None:
    """Display the execution result + current progress at the same line with the method / path names."""
    ctx.operations_processed += 1
    ctx.results.append(event.result)
    display_execution_result(ctx, event.status)
    display_percentage(ctx, event)


def on_engine_finished(ctx: ExecutionContext, event: events.EngineFinished) -> None:
    """Show the outcome of the whole testing session."""
    click.echo()
    display_errors(ctx)
    display_failures(ctx, event)
    display_statistic(ctx, event)
    if ctx.summary_lines:
        click.echo()
        _print_lines(ctx.summary_lines)
    click.echo()
    display_summary(ctx, event)


def _print_lines(lines: list[str | Generator[str, None, None]]) -> None:
    for entry in lines:
        if isinstance(entry, str):
            click.echo(entry)
        elif isinstance(entry, GeneratorType):
            for line in entry:
                click.echo(line)


def on_interrupted(ctx: ExecutionContext, event: events.Interrupted) -> None:
    click.echo()
    _handle_interrupted(ctx)


def _handle_interrupted(ctx: ExecutionContext) -> None:
    ctx.is_interrupted = True
    display_section_name("KeyboardInterrupt", "!", bold=False)


def on_internal_error(ctx: ExecutionContext, event: events.FatalError) -> None:
    display_internal_error(ctx, event)
    raise click.Abort


def on_stateful_test_event(ctx: ExecutionContext, event: events.TestEvent) -> None:
    if isinstance(event, events.ScenarioFinished) and not event.is_final:
        if event.status == Status.INTERRUPTED:
            _handle_interrupted(ctx)
        elif event.status is not None:
            display_execution_result(ctx, event.status)
    # It is initialized in `PhaseStarted`
    sink = cast(StateMachineSink, ctx.state_machine_sink)
    sink.consume(event)


class DefaultOutputStyleHandler(EventHandler):
    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        """Choose and execute a proper handler for the given event."""
        from schemathesis.runner.phases import PhaseName
        from schemathesis.runner.phases.stateful import StatefulTestingPayload

        if isinstance(event, events.Initialized):
            on_initialized(ctx, event)
        elif isinstance(event, events.PhaseStarted):
            if event.phase.name == PhaseName.PROBING:
                on_probing_started()
            elif event.phase.name == PhaseName.STATEFUL_TESTING and event.phase.is_enabled:
                on_stateful_testing_started(ctx)
        elif isinstance(event, events.PhaseFinished):
            if event.phase.name == PhaseName.PROBING:
                on_probing_finished(ctx, event.status)
            elif event.phase.name == PhaseName.STATEFUL_TESTING and event.phase.is_enabled:
                assert isinstance(event.payload, StatefulTestingPayload) or event.payload is None
                on_stateful_testing_finished(ctx, event.payload)
                if not ctx.is_interrupted:
                    click.echo()
        elif isinstance(event, events.NonFatalError):
            ctx.errors.append(event)
        elif isinstance(event, events.Warning):
            ctx.warnings.append(event.message)
        elif isinstance(event, events.BeforeExecution):
            on_before_execution(ctx, event)
        elif isinstance(event, events.AfterExecution):
            on_after_execution(ctx, event)
        elif isinstance(event, events.EngineFinished):
            on_engine_finished(ctx, event)
        elif isinstance(event, events.Interrupted):
            on_interrupted(ctx, event)
        elif isinstance(event, events.FatalError):
            on_internal_error(ctx, event)
        elif isinstance(event, events.TestEvent):
            if event.phase == PhaseName.STATEFUL_TESTING:
                on_stateful_test_event(ctx, event)
