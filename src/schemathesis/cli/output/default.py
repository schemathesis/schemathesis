import base64
import os
import platform
import shutil
from typing import Any, Dict, List, Optional, Tuple, Union, cast

import click
from hypothesis import settings

from ..._compat import metadata
from ...constants import CodeSampleStyle, __version__
from ...models import Response, Status
from ...runner import events
from ...runner.serialization import SerializedCase, SerializedError, SerializedTestResult
from ..context import ExecutionContext
from ..handlers import EventHandler, get_unique_failures

DISABLE_SCHEMA_VALIDATION_MESSAGE = (
    "\nYou can disable input schema validation with --validate-schema=false "
    "command-line option\nIn this case, Schemathesis cannot guarantee proper"
    " behavior during the test run"
)


def get_terminal_width() -> int:
    # Some CI/CD providers (e.g. CircleCI) return a (0, 0) terminal size so provide a default
    return shutil.get_terminal_size((80, 24)).columns


def display_section_name(title: str, separator: str = "=", extra: str = "", **kwargs: Any) -> None:
    """Print section name with separators in terminal with the given title nicely centered."""
    extra = extra if not extra else f" [{extra}]"
    message = f" {title}{extra} ".center(get_terminal_width(), separator)
    kwargs.setdefault("bold", True)
    click.secho(message, **kwargs)


def display_subsection(result: SerializedTestResult, color: Optional[str] = "red") -> None:
    section_name = f"{result.method} {result.path}" if result.verbose_name is None else result.verbose_name
    display_section_name(section_name, "_", result.data_generation_method, fg=color)


def get_percentage(position: int, length: int) -> str:
    """Format completion percentage in square brackets."""
    percentage_message = f"{position * 100 // length}%".rjust(4)
    return f"[{percentage_message}]"


def display_execution_result(context: ExecutionContext, event: events.AfterExecution) -> None:
    """Display an appropriate symbol for the given event's execution result."""
    symbol, color = {Status.success: (".", "green"), Status.failure: ("F", "red"), Status.error: ("E", "red")}[
        event.status
    ]
    context.current_line_length += len(symbol)
    click.secho(symbol, nl=False, fg=color)


def display_percentage(context: ExecutionContext, event: events.AfterExecution) -> None:
    """Add the current progress in % to the right side of the current line."""
    operations_count = cast(int, context.operations_count)  # is already initialized via `Initialized` event
    current_percentage = get_percentage(context.operations_processed, operations_count)
    styled = click.style(current_percentage, fg="cyan")
    # Total length of the message, so it will fill to the right border of the terminal.
    # Padding is already taken into account in `context.current_line_length`
    length = max(get_terminal_width() - context.current_line_length + len(styled) - len(current_percentage), 1)
    template = f"{{:>{length}}}"
    click.echo(template.format(styled))


def display_summary(event: events.Finished) -> None:
    message, color = get_summary_output(event)
    display_section_name(message, fg=color)


def get_summary_message_parts(event: events.Finished) -> List[str]:
    parts = []
    passed = event.passed_count
    if passed:
        parts.append(f"{passed} passed")
    failed = event.failed_count
    if failed:
        parts.append(f"{failed} failed")
    errored = event.errored_count
    if errored:
        parts.append(f"{errored} errored")
    return parts


def get_summary_output(event: events.Finished) -> Tuple[str, str]:
    parts = get_summary_message_parts(event)
    if not parts:
        message = "Empty test suite"
        color = "yellow"
    else:
        message = f'{", ".join(parts)} in {event.running_time:.2f}s'
        if event.has_failures or event.has_errors:
            color = "red"
        else:
            color = "green"
    return message, color


def display_hypothesis_output(hypothesis_output: List[str]) -> None:
    """Show falsifying examples from Hypothesis output if there are any."""
    if hypothesis_output:
        display_section_name("HYPOTHESIS OUTPUT")
        output = "\n".join(hypothesis_output)
        click.secho(output, fg="red")


def display_errors(context: ExecutionContext, event: events.Finished) -> None:
    """Display all errors in the test run."""
    if not event.has_errors:
        return

    display_section_name("ERRORS")
    should_display_full_traceback_message = False
    for result in context.results:
        if not result.has_errors:
            continue
        should_display_full_traceback_message |= display_single_error(context, result)
    if event.generic_errors:
        display_generic_errors(context, event.generic_errors)
    if should_display_full_traceback_message and not context.show_errors_tracebacks:
        click.secho(
            "Add this option to your command line parameters to see full tracebacks: --show-errors-tracebacks", fg="red"
        )


def display_single_error(context: ExecutionContext, result: SerializedTestResult) -> bool:
    display_subsection(result)
    should_display_full_traceback_message = False
    for error in result.errors:
        should_display_full_traceback_message |= _display_error(context, error, result.seed)
    return should_display_full_traceback_message


def display_generic_errors(context: ExecutionContext, errors: List[SerializedError]) -> None:
    for error in errors:
        display_section_name(error.title or "Generic error", "_", fg="red")
        _display_error(context, error)


def display_full_traceback_message(exception: str) -> bool:
    # Some errors should not trigger the message that suggests to show full tracebacks to the user
    return not exception.startswith("DeadlineExceeded")


def _display_error(context: ExecutionContext, error: SerializedError, seed: Optional[int] = None) -> bool:
    if context.show_errors_tracebacks:
        message = error.exception_with_traceback
    else:
        message = error.exception
    if error.exception.startswith("InvalidSchema") and context.validate_schema:
        message += DISABLE_SCHEMA_VALIDATION_MESSAGE + "\n"
    if error.exception.startswith("DeadlineExceeded"):
        message += (
            "Consider extending the deadline with the `--hypothesis-deadline` CLI option.\n"
            "You can disable it completely with `--hypothesis-deadline=None`.\n"
        )
    click.secho(message, fg="red")
    if error.example is not None:
        display_example(context, error.example, seed=seed)
    return display_full_traceback_message(error.exception)


def display_failures(context: ExecutionContext, event: events.Finished) -> None:
    """Display all failures in the test run."""
    if not event.has_failures:
        return
    relevant_results = [result for result in context.results if not result.is_errored]
    if not relevant_results:
        return
    display_section_name("FAILURES")
    for result in relevant_results:
        if not result.has_failures:
            continue
        display_failures_for_single_test(context, result)


def display_failures_for_single_test(context: ExecutionContext, result: SerializedTestResult) -> None:
    """Display a failure for a single method / path."""
    display_subsection(result)
    checks = get_unique_failures(result.checks)
    for idx, check in enumerate(checks, 1):
        message: Optional[str]
        if check.message:
            message = f"{idx}. {check.message}"
        else:
            message = None
        display_example(context, check.example, check.response, message, result.seed)
        # Display every time except the last check
        if idx != len(checks):
            click.echo("\n")


def reduce_schema_error(message: str) -> str:
    """Reduce the error schema output."""
    end_of_message_index = message.find(":", message.find("Failed validating"))
    if end_of_message_index != -1:
        return message[:end_of_message_index]
    return message


def display_example(
    context: ExecutionContext,
    case: SerializedCase,
    response: Optional[Response] = None,
    message: Optional[str] = None,
    seed: Optional[int] = None,
) -> None:
    if message is not None:
        if not context.verbosity:
            message = reduce_schema_error(message)
        click.secho(message, fg="red")
        click.echo()
    for line in case.text_lines:
        click.secho(line, fg="red")
    click.echo()
    if response is not None and response.body is not None:
        payload = base64.b64decode(response.body).decode(response.encoding or "utf8", errors="replace")
        click.secho(f"----------\n\nResponse payload: `{payload}`\n", fg="red")
    if context.code_sample_style == CodeSampleStyle.python:
        click.secho(f"Run this Python code to reproduce this failure: \n\n    {case.requests_code}\n", fg="red")
    if context.code_sample_style == CodeSampleStyle.curl:
        click.secho(f"Run this cURL command to reproduce this failure: \n\n    {case.curl_code}\n", fg="red")
    if seed is not None:
        click.secho(f"Or add this option to your command line parameters: --hypothesis-seed={seed}", fg="red")


def display_application_logs(context: ExecutionContext, event: events.Finished) -> None:
    """Print logs captured during the application run."""
    if not event.has_logs:
        return
    display_section_name("APPLICATION LOGS")
    for result in context.results:
        if not result.has_logs:
            continue
        display_single_log(result)


def display_single_log(result: SerializedTestResult) -> None:
    display_subsection(result, None)
    click.echo("\n\n".join(result.logs))


def display_statistic(context: ExecutionContext, event: events.Finished) -> None:
    """Format and print statistic collected by :obj:`models.TestResult`."""
    display_section_name("SUMMARY")
    click.echo()
    total = event.total
    if event.is_empty or not total:
        click.secho("No checks were performed.", bold=True)

    if total:
        display_checks_statistics(total)

    if context.cassette_file_name or context.junit_xml_file:
        click.echo()

    if context.cassette_file_name:
        category = click.style("Network log", bold=True)
        click.secho(f"{category}: {context.cassette_file_name}")

    if context.junit_xml_file:
        category = click.style("JUnit XML file", bold=True)
        click.secho(f"{category}: {context.junit_xml_file}")


def display_checks_statistics(total: Dict[str, Dict[Union[str, Status], int]]) -> None:
    padding = 20
    col1_len = max(map(len, total.keys())) + padding
    col2_len = len(str(max(total.values(), key=lambda v: v["total"])["total"])) * 2 + padding
    col3_len = padding
    click.secho("Performed checks:", bold=True)
    template = f"    {{:{col1_len}}}{{:{col2_len}}}{{:{col3_len}}}"
    for check_name, results in total.items():
        display_check_result(check_name, results, template)


def display_check_result(check_name: str, results: Dict[Union[str, Status], int], template: str) -> None:
    """Show results of single check execution."""
    if Status.failure in results:
        verdict = "FAILED"
        color = "red"
    else:
        verdict = "PASSED"
        color = "green"
    success = results.get(Status.success, 0)
    total = results.get("total", 0)
    click.echo(template.format(check_name, f"{success} / {total} passed", click.style(verdict, fg=color, bold=True)))


def display_internal_error(context: ExecutionContext, event: events.InternalError) -> None:
    click.secho(event.message, fg="red")
    if event.exception:
        if context.show_errors_tracebacks:
            message = event.exception_with_traceback
        else:
            message = event.exception
        message = (
            f"Error: {message}\n"
            f"Add this option to your command line parameters to see full tracebacks: --show-errors-tracebacks"
        )
        if event.exception_type == "jsonschema.exceptions.ValidationError":
            message += "\n" + DISABLE_SCHEMA_VALIDATION_MESSAGE
        click.secho(message, fg="red")


def handle_initialized(context: ExecutionContext, event: events.Initialized) -> None:
    """Display information about the test session."""
    context.operations_count = cast(int, event.operations_count)  # INVARIANT: should not be `None`
    display_section_name("Schemathesis test session starts")
    versions = (
        f"platform {platform.system()} -- "
        f"Python {platform.python_version()}, "
        f"schemathesis-{__version__}, "
        f"hypothesis-{metadata.version('hypothesis')}, "
        f"hypothesis_jsonschema-{metadata.version('hypothesis_jsonschema')}, "
        f"jsonschema-{metadata.version('jsonschema')}"
    )
    click.echo(versions)
    click.echo(f"rootdir: {os.getcwd()}")
    click.echo(
        f"hypothesis profile '{settings._current_profile}' "  # type: ignore
        f"-> {settings.get_profile(settings._current_profile).show_changed()}"
    )
    if event.location is not None:
        click.echo(f"Schema location: {event.location}")
    click.echo(f"Base URL: {event.base_url}")
    click.echo(f"Specification version: {event.specification_name}")
    click.echo(f"Workers: {context.workers_num}")
    click.secho(f"Collected API operations: {context.operations_count}", bold=True)
    if context.operations_count >= 1:
        click.echo()


TRUNCATION_PLACEHOLDER = "[...]"


def handle_before_execution(context: ExecutionContext, event: events.BeforeExecution) -> None:
    """Display what method / path will be tested next."""
    # We should display execution result + percentage in the end. For example:
    max_length = get_terminal_width() - len(" . [XXX%]") - len(TRUNCATION_PLACEHOLDER)
    message = event.verbose_name
    if event.recursion_level > 0:
        message = f"{'    ' * event.recursion_level}-> {message}"
        # This value is not `None` - the value is set in runtime before this line
        context.operations_count += 1  # type: ignore

    message = message[:max_length] + (message[max_length:] and "[...]") + " "
    context.current_line_length = len(message)
    click.echo(message, nl=False)


def handle_after_execution(context: ExecutionContext, event: events.AfterExecution) -> None:
    """Display the execution result + current progress at the same line with the method / path names."""
    context.operations_processed += 1
    context.results.append(event.result)
    display_execution_result(context, event)
    display_percentage(context, event)


def handle_finished(context: ExecutionContext, event: events.Finished) -> None:
    """Show the outcome of the whole testing session."""
    click.echo()
    display_hypothesis_output(context.hypothesis_output)
    display_errors(context, event)
    display_failures(context, event)
    display_application_logs(context, event)
    display_statistic(context, event)
    click.echo()
    display_summary(event)


def handle_interrupted(context: ExecutionContext, event: events.Interrupted) -> None:
    click.echo()
    display_section_name("KeyboardInterrupt", "!", bold=False)


def handle_internal_error(context: ExecutionContext, event: events.InternalError) -> None:
    display_internal_error(context, event)
    raise click.Abort


class DefaultOutputStyleHandler(EventHandler):
    def handle_event(self, context: ExecutionContext, event: events.ExecutionEvent) -> None:
        """Choose and execute a proper handler for the given event."""
        if isinstance(event, events.Initialized):
            handle_initialized(context, event)
        if isinstance(event, events.BeforeExecution):
            handle_before_execution(context, event)
        if isinstance(event, events.AfterExecution):
            context.hypothesis_output.extend(event.hypothesis_output)
            handle_after_execution(context, event)
        if isinstance(event, events.Finished):
            handle_finished(context, event)
        if isinstance(event, events.Interrupted):
            handle_interrupted(context, event)
        if isinstance(event, events.InternalError):
            handle_internal_error(context, event)
