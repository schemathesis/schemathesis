import os
import platform
import shutil
import traceback
from typing import Any, Dict, List, Union

import click
from hypothesis import settings
from importlib_metadata import version

from ..constants import __version__
from ..models import Case, Status, TestResult, TestResultSet
from ..runner import events


def get_terminal_width() -> int:
    return shutil.get_terminal_size().columns


def display_section_name(title: str, separator: str = "=", **kwargs: Any) -> None:
    """Print section name with separators in terminal with the given title nicely centered."""
    message = f" {title} ".center(get_terminal_width(), separator)
    kwargs.setdefault("bold", True)
    click.secho(message, **kwargs)


def display_subsection(result: TestResult) -> None:
    section_name = f"{result.method}: {result.path}"
    display_section_name(section_name, "_", fg="red")


def get_percentage(position: int, length: int) -> str:
    """Format completion percentage in square brackets."""
    percentage_message = f"{position * 100 // length}%".rjust(4)
    return f"[{percentage_message}]"


def handle_initialized(context: events.ExecutionContext, event: events.Initialized) -> None:
    """Display information about the test session."""
    display_section_name("Schemathesis test session starts")
    versions = (
        f"platform {platform.system()} -- "
        f"Python {platform.python_version()}, "
        f"schemathesis-{__version__}, "
        f"hypothesis-{version('hypothesis')}, "
        f"hypothesis_jsonschema-{version('hypothesis_jsonschema')}, "
        f"jsonschema-{version('jsonschema')}"
    )
    click.echo(versions)
    click.echo(f"rootdir: {os.getcwd()}")
    click.echo(
        f"hypothesis profile '{settings._current_profile}' "  # type: ignore
        f"-> {settings.get_profile(settings._current_profile).show_changed()}"
    )
    click.secho(f"collected endpoints: {event.schema.endpoints_count}\n", bold=True)


def handle_before_execution(context: events.ExecutionContext, event: events.BeforeExecution) -> None:
    """Display what method / endpoint will be tested next."""
    message = f"{event.endpoint.method} {event.endpoint.path} "
    context.current_line_length = len(message)
    click.echo(message, nl=False)


def handle_after_execution(context: events.ExecutionContext, event: events.AfterExecution) -> None:
    """Display the execution result + current progress at the same line with the method / endpoint names."""
    context.endpoints_processed += 1
    display_execution_result(context, event)
    display_percentage(context, event)


def display_execution_result(context: events.ExecutionContext, event: events.AfterExecution) -> None:
    """Display an appropriate symbol for the given event's execution result."""
    if event.status == Status.failure:
        symbol, color = "F", "red"
    elif event.status == Status.error:
        symbol, color = "E", "red"
    else:
        symbol, color = ".", "green"
    context.current_line_length += len(symbol)
    click.secho(symbol, nl=False, fg=color)


def display_percentage(context: events.ExecutionContext, event: events.AfterExecution) -> None:
    """Add the current progress in % to the right side of the current line."""
    padding = 1
    current_percentage = get_percentage(context.endpoints_processed, event.schema.endpoints_count)
    styled = click.style(current_percentage, fg="cyan")
    # Total length of the message so it will fill to the right border of the terminal minus padding
    length = get_terminal_width() - context.current_line_length + len(styled) - len(current_percentage) - padding
    template = f"{{:>{length}}}"
    click.echo(template.format(styled))


def handle_finished(context: events.ExecutionContext, event: events.Finished) -> None:
    """Show the outcome of the whole testing session."""
    click.echo()
    display_hypothesis_output(context.hypothesis_output)
    display_errors(event.results)
    display_failures(event.results)
    display_statistic(event.results)
    click.echo()

    if event.results.has_failures or event.results.has_errors:
        click.secho("Tests failed.", fg="red")
        raise click.exceptions.Exit(1)

    click.secho("Tests succeeded.", fg="green")


def display_hypothesis_output(hypothesis_output: List[str]) -> None:
    """Show falsifying examples from Hypothesis output if there are any."""
    if hypothesis_output:
        display_section_name("HYPOTHESIS OUTPUT")
        output = "\n".join(hypothesis_output)
        click.secho(output, fg="red")


def display_errors(results: TestResultSet) -> None:
    """Display all errors in the test run."""
    if not results.has_errors:
        return

    display_section_name("ERRORS")
    for result in results.results:
        if not result.has_errors:
            continue
        display_single_error(result)


def display_single_error(result: TestResult) -> None:
    display_subsection(result)
    for error in result.errors:
        message = "".join(traceback.format_exception_only(type(error), error))
        click.secho(message)


def display_failures(results: TestResultSet) -> None:
    """Display all failures in the test run."""
    if not results.has_failures:
        return

    display_section_name("FAILURES")
    for result in results.results:
        if not result.has_failures:
            continue
        display_single_failure(result)


def display_single_failure(result: TestResult) -> None:
    """Display a failure for a single method / endpoint."""
    display_subsection(result)
    for check in reversed(result.checks):
        if check.example is not None:
            output = {
                make_verbose_name(attribute): getattr(check.example, attribute.name)
                for attribute in Case.__attrs_attrs__  # type: ignore
                if attribute.name not in ("path", "method", "base_url")
            }
            max_length = max(map(len, output))
            template = f"{{:<{max_length}}} : {{}}"
            click.secho(template.format("Check", check.name), fg="red")
            for key, value in output.items():
                if (key == "Body" and value is not None) or value not in (None, {}):
                    click.secho(template.format(key, value), fg="red")
            # Display only the latest case
            # (dd): It is possible to find multiple errors, but the simplest option for now is to display
            # the latest and avoid deduplication, which will be done in the future.
            break


def make_verbose_name(attribute: Attribute) -> str:
    return attribute.name.capitalize().replace("_", " ")


def display_statistic(statistic: TestResultSet) -> None:
    """Format and print statistic collected by :obj:`models.TestResult`."""
    display_section_name("SUMMARY")
    click.echo()
    total = statistic.total
    if statistic.is_empty or not total:
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
    click.echo(
        template.format(
            click.style(check_name, bold=True),
            f"{results[Status.success]} / {results['total']} passed",
            click.style(verdict, fg=color, bold=True),
        )
    )


def handle_event(context: events.ExecutionContext, event: events.ExecutionEvent) -> None:
    """Choose and execute a proper handler for the given event."""
    if isinstance(event, events.Initialized):
        handle_initialized(context, event)
    if isinstance(event, events.BeforeExecution):
        handle_before_execution(context, event)
    if isinstance(event, events.AfterExecution):
        handle_after_execution(context, event)
    if isinstance(event, events.Finished):
        handle_finished(context, event)
