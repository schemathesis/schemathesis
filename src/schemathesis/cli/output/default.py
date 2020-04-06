import os
import platform
import shutil
from typing import Any, Dict, List, Optional, Set, Tuple, Union, cast

import click
from hypothesis import settings

from ..._compat import metadata
from ...constants import __version__
from ...models import Status
from ...runner import events
from ...runner.serialization import SerializedCase, SerializedCheck, SerializedTestResult
from ..context import ExecutionContext


def get_terminal_width() -> int:
    return shutil.get_terminal_size().columns


def display_section_name(title: str, separator: str = "=", **kwargs: Any) -> None:
    """Print section name with separators in terminal with the given title nicely centered."""
    message = f" {title} ".center(get_terminal_width(), separator)
    kwargs.setdefault("bold", True)
    click.secho(message, **kwargs)


def display_subsection(result: SerializedTestResult, color: Optional[str] = "red") -> None:
    section_name = f"{result.method}: {result.path}"
    display_section_name(section_name, "_", fg=color)


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
    padding = 1
    endpoints_count = cast(int, context.endpoints_count)  # is already initialized via `Initialized` event
    current_percentage = get_percentage(context.endpoints_processed, endpoints_count)
    styled = click.style(current_percentage, fg="cyan")
    # Total length of the message so it will fill to the right border of the terminal minus padding
    length = get_terminal_width() - context.current_line_length + len(styled) - len(current_percentage) - padding
    template = f"{{:>{length}}}"
    click.echo(template.format(styled))


def display_summary(event: events.Finished) -> None:
    message, color, status_code = get_summary_output(event)
    display_section_name(message, fg=color)
    raise click.exceptions.Exit(status_code)


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


def get_summary_output(event: events.Finished) -> Tuple[str, str, int]:
    parts = get_summary_message_parts(event)
    if not parts:
        message = "Empty test suite"
        color = "yellow"
        status_code = 0
    else:
        message = f'{", ".join(parts)} in {event.running_time:.2f}s'
        if event.has_failures or event.has_errors:
            color = "red"
            status_code = 1
        else:
            color = "green"
            status_code = 0
    return message, color, status_code


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
    for result in context.results:
        if not result.has_errors:
            continue
        display_single_error(context, result)
    if not context.show_errors_tracebacks:
        click.secho(
            "Add this option to your command line parameters to see full tracebacks: --show-errors-tracebacks", fg="red"
        )


def display_single_error(context: ExecutionContext, result: SerializedTestResult) -> None:
    display_subsection(result)
    for error in result.errors:
        if context.show_errors_tracebacks:
            message = error.exception_with_traceback
        else:
            message = error.exception
        click.secho(message, fg="red")
        if error.example is not None:
            display_example(error.example, seed=result.seed)


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
        display_failures_for_single_test(result)


def display_failures_for_single_test(result: SerializedTestResult) -> None:
    """Display a failure for a single method / endpoint."""
    display_subsection(result)
    checks = _get_unique_failures(result.checks)
    for idx, check in enumerate(checks, 1):
        message: Optional[str]
        if check.message:
            message = f"{idx}. {check.message}"
        else:
            message = None
        example = cast(SerializedCase, check.example)  # filtered in `_get_unique_failures`
        display_example(example, check.name, message, result.seed)
        # Display every time except the last check
        if idx != len(checks):
            click.echo("\n")


def _get_unique_failures(checks: List[SerializedCheck]) -> List[SerializedCheck]:
    """Return only unique checks that should be displayed in the output."""
    seen: Set[Tuple[str, Optional[str]]] = set()
    unique_checks = []
    for check in reversed(checks):
        # There are also could be checks that didn't fail
        if check.example is not None and check.value == Status.failure and (check.name, check.message) not in seen:
            unique_checks.append(check)
            seen.add((check.name, check.message))
    return unique_checks


def display_example(
    case: SerializedCase, check_name: Optional[str] = None, message: Optional[str] = None, seed: Optional[int] = None
) -> None:
    if message is not None:
        click.secho(message, fg="red")
        click.echo()
    output = {
        make_verbose_name(attribute): getattr(case, attribute)
        for attribute in ("path_parameters", "headers", "cookies", "query", "body", "form_data")
    }
    max_length = max(map(len, output))
    template = f"{{:<{max_length}}} : {{}}"
    if check_name is not None:
        click.secho(template.format("Check", check_name), fg="red")
    for key, value in output.items():
        if (key == "Body" and value is not None) or value not in (None, {}):
            click.secho(template.format(key, value), fg="red")
    click.echo()
    click.secho(f"Run this Python code to reproduce this failure: \n\n    {case.requests_code}", fg="red")
    if seed is not None:
        click.secho(f"\nOr add this option to your command line parameters: --hypothesis-seed={seed}", fg="red")


def make_verbose_name(attribute: str) -> str:
    return attribute.capitalize().replace("_", " ")


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


def display_statistic(event: events.Finished) -> None:
    """Format and print statistic collected by :obj:`models.TestResult`."""
    display_section_name("SUMMARY")
    click.echo()
    total = event.total
    if event.is_empty or not total:
        click.secho("No checks were performed.", bold=True)
        return

    padding = 20
    col1_len = max(map(len, total.keys())) + padding
    col2_len = len(str(max(total.values(), key=lambda v: v["total"])["total"])) * 2 + padding
    col3_len = padding

    template = f"{{:{col1_len}}}{{:{col2_len}}}{{:{col3_len}}}"

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
    click.echo(
        template.format(
            click.style(check_name, bold=True), f"{success} / {total} passed", click.style(verdict, fg=color, bold=True)
        )
    )


def display_internal_error(context: ExecutionContext, event: events.InternalError) -> None:
    click.secho(event.message, fg="red")
    if event.exception:
        if context.show_errors_tracebacks:
            message = event.exception_with_traceback
        else:
            message = event.exception
        click.secho(f"Error: {message}", fg="red")


def handle_initialized(context: ExecutionContext, event: events.Initialized) -> None:
    """Display information about the test session."""
    context.endpoints_count = event.endpoints_count
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
    if event.base_url is not None:
        click.echo(f"Base URL: {event.base_url}")
    click.echo(f"Specification version: {event.specification_name}")
    click.echo(f"Workers: {context.workers_num}")
    click.secho(f"collected endpoints: {event.endpoints_count}", bold=True)
    if event.endpoints_count >= 1:
        click.echo()


def handle_before_execution(context: ExecutionContext, event: events.BeforeExecution) -> None:
    """Display what method / endpoint will be tested next."""
    message = f"{event.method} {event.path} "
    context.current_line_length = len(message)
    click.echo(message, nl=False)


def handle_after_execution(context: ExecutionContext, event: events.AfterExecution) -> None:
    """Display the execution result + current progress at the same line with the method / endpoint names."""
    context.endpoints_processed += 1
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
    display_statistic(event)
    click.echo()
    display_summary(event)


def handle_interrupted(context: ExecutionContext, event: events.Interrupted) -> None:
    click.echo()
    display_section_name("KeyboardInterrupt", "!", bold=False)


def handle_internal_error(context: ExecutionContext, event: events.InternalError) -> None:
    display_internal_error(context, event)
    raise click.Abort


def handle_event(context: ExecutionContext, event: events.ExecutionEvent) -> None:
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
