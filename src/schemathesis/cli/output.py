import os
import platform
import shutil
from typing import Counter, List

import click
from hypothesis import settings
from importlib_metadata import version

from .. import runner
from ..constants import __version__
from ..runner import events


def get_terminal_width() -> int:
    return shutil.get_terminal_size().columns


def display_section_name(title: str, separator: str = "=", bold: bool = True) -> None:
    """Print section name with separators in terminal with the given title nicely centered."""
    message = f" {title} ".center(get_terminal_width(), separator)
    click.secho(message, bold=bold)


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
    if event.result == runner.events.ExecutionResult.failure:
        symbol, color = "F", "red"
    elif event.result == runner.events.ExecutionResult.error:
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
    display_statistic(event.statistic)
    click.echo()

    if event.statistic.has_errors:
        click.secho("Tests failed.", fg="red")
        raise click.exceptions.Exit(1)

    click.secho("Tests succeeded.", fg="green")


def display_hypothesis_output(hypothesis_output: List[str]) -> None:
    """Show falsifying examples from Hypothesis output if there are any."""
    if hypothesis_output:
        display_section_name("HYPOTHESIS OUTPUT")
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

    display_section_name("SUMMARY")
    click.echo()
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
