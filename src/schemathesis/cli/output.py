import os
import platform
import shutil
from contextlib import contextmanager
from enum import IntEnum
from typing import Counter, Generator, List, Optional

import click
from hypothesis import settings
from importlib_metadata import version

from .. import runner
from ..constants import __version__
from ..runner import events


class ColumnWidth(IntEnum):
    """Width of different columns in the output."""

    method = 8
    endpoint = 10
    result = 4


def get_terminal_width() -> int:
    return shutil.get_terminal_size().columns


@contextmanager
def print_in_section(
    title: str, separator: str = "-", start_newline: bool = False, line_length: Optional[int] = None
) -> Generator[None, None, None]:
    """Print section in terminal with the given title nicely centered.

    Usage::

        with print_in_section("statistics"):
            print("Number of items:", len(items))
    """
    if start_newline:
        click.echo()

    line_length = line_length or get_terminal_width()

    click.echo(f" {title} ".center(line_length, separator))
    yield
    click.echo(separator * line_length)


def get_percentage(position: int, length: int) -> str:
    """Format completion percentage in square brackets."""
    percentage_message = f"{position * 100 // length}%".rjust(4)
    return f"[{percentage_message}]"


def handle_initialized(context: events.ExecutionContext, event: events.Initialized) -> None:
    """Display information about the test session."""
    with print_in_section("test session starts"):
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
        click.echo(f"Collected endpoints: {event.schema.endpoints_count}")


def handle_before_execution(context: events.ExecutionContext, event: events.BeforeExecution) -> None:
    """Display what method / endpoint will be tested next."""
    # Print test method and endpoint before test execution
    col2_len = len(event.endpoint.path)
    template = f"    {{:<{ColumnWidth.method}}} {{:<{col2_len}}} "
    click.echo(template.format(event.endpoint.method, event.endpoint.path), nl=False)


def handle_after_execution(context: events.ExecutionContext, event: events.AfterExecution) -> None:
    """Display the execution result + current progress at the same line with the method / endpoint names."""
    context.current_position += 1
    display_execution_result(event)
    display_percentage(context, event)


def display_execution_result(event: events.AfterExecution) -> None:
    """Display an appropriate symbol for the given event's execution result."""
    template = f"{{:{ColumnWidth.result}}} "
    if event.result == runner.events.ExecutionResult.failure:
        symbol, color = "F", "red"
    elif event.result == runner.events.ExecutionResult.error:
        symbol, color = "E", "red"
    else:
        symbol, color = ".", "green"
    click.secho(template.format(symbol), nl=False, fg=color)


def display_percentage(context: events.ExecutionContext, event: events.AfterExecution) -> None:
    """Add the current progress in % to the right side of the current line."""
    padding = 10
    percentage_length = get_terminal_width() - ColumnWidth.method - ColumnWidth.endpoint - ColumnWidth.result - padding
    current_percentage = get_percentage(context.current_position, event.schema.endpoints_count)
    message = f"{{:>{percentage_length}}}".format(click.style(current_percentage, fg="cyan"))  # type:ignore
    click.echo(message)


def handle_finished(context: events.ExecutionContext, event: events.Finished) -> None:
    """Show the outcome of the whole testing session."""
    display_falsifying_examples(context.hypothesis_output)
    display_statistic(event.statistic)
    click.echo()

    if event.statistic.has_errors:
        click.secho("Tests failed.", fg="red")
        raise click.exceptions.Exit(1)

    click.secho("Tests succeeded.", fg="green")


def display_falsifying_examples(hypothesis_output: List[str]) -> None:
    """Show falsifying examples from Hypothesis output if there are any."""
    if hypothesis_output:
        with print_in_section("FALSIFYING EXAMPLES", start_newline=True):
            output = "\n".join(hypothesis_output)
            click.secho(output, fg="red")


def display_statistic(statistic: runner.StatsCollector) -> None:
    """Format and print statistic collected by :obj:`runner.StatsCollector`."""
    if statistic.is_empty:
        click.secho("No checks were performed.", bold=True)
        return

    padding = 20
    col1_len = max(map(len, statistic.data.keys())) + padding
    col2_len = len(str(max(statistic.data.values(), key=lambda v: v["total"])["total"])) * 2 + padding
    col3_len = padding

    template = f"{{:{col1_len}}}{{:{col2_len}}}{{:{col3_len}}}"

    with print_in_section("SUMMARY", start_newline=True):
        for check_name, results in statistic.data.items():
            display_check_result(check_name, results, template)


def display_check_result(check_name: str, results: Counter, template: str) -> None:
    """Show results of single check execution."""
    if results["error"]:
        verdict = "FAILED"
        color = "red"
    else:
        verdict = "PASSED"
        color = "green"
    click.echo(
        template.format(
            click.style(check_name, bold=True),
            f"{results['ok']} / {results['total']} passed",
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
