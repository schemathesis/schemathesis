import os
import platform
import shutil
from contextlib import contextmanager
from typing import Generator, List, Optional

import click
from hypothesis import settings
from importlib_metadata import version

from .. import runner
from ..constants import __version__
from ..models import StatsCollector
from ..runner import events


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

    line_length = line_length or shutil.get_terminal_size((100, 50)).columns

    click.echo(f" {title} ".center(line_length, separator))
    yield
    click.echo(separator * line_length)


def pretty_print_stats(stats: runner.StatsCollector, hypothesis_output: Optional[List[str]] = None) -> None:
    """Format and print stats collected by :obj:`runner.StatsCollector`."""
    if hypothesis_output:
        with print_in_section("FALSIFYING EXAMPLES", start_newline=True):
            output = "\n".join(hypothesis_output)
            click.secho(output, fg="red")

    if stats.is_empty:
        click.secho("No checks were performed.", bold=True)
        return

    padding = 20
    col1_len = max(map(len, stats.data.keys())) + padding
    col2_len = len(str(max(stats.data.values(), key=lambda v: v["total"])["total"])) * 2 + padding
    col3_len = padding

    template = f"{{:{col1_len}}}{{:{col2_len}}}{{:{col3_len}}}"

    with print_in_section("SUMMARY", start_newline=True):
        for check_name, results in stats.data.items():
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


def percentage(position: int, length: int) -> str:
    return f"[{position * 100 // length}%]"


def pretty_print_test_progress(results_generator: Generator[events.ExecutionEvent, None, None]) -> StatsCollector:
    # Starting progress position
    position = 0
    errors = 0
    # Terminal size and fields position
    columns = shutil.get_terminal_size((100, 50)).columns
    col1_len = 8  # Method column
    col2_len = 10  # Test endpoint column
    col3_len = 4  # Test result column
    col4_len = 10  # Progress column
    # Initial runner state
    init = next(results_generator)
    print_header(init.schema.endpoints_count)  # type: ignore
    for result in results_generator:
        # Print test method and endpoint before test execution
        if isinstance(result, events.BeforeExecution):
            # Collect errors count before test execution
            errors = sum([check["error"] for check in result.statistic.data.values()])
            # Re-calculate fields position
            col2_len = len(result.endpoint.path)
            template = f"    {{:<{col1_len}}} {{:<{col2_len}}} "
            click.echo(template.format(result.endpoint.method, result.endpoint.path), nl=False)
        # Print test result and progress after execution
        if isinstance(result, (events.AfterExecution, events.FailedExecution)):
            position += 1
            # Recalculate number of errors to obtain test state after execution
            new_errors = sum([check["error"] for check in result.statistic.data.values()])
            template = f"{{:{col3_len}}} "
            if new_errors > errors:
                click.secho(template.format("F"), nl=False, fg="red")
            elif isinstance(result, events.FailedExecution):
                click.secho(template.format("E"), nl=False, fg="red")
            else:
                click.secho(template.format("."), nl=False, fg="green")
            col4_len = int(columns) - col1_len - col2_len - col3_len - 10
            click.secho(
                f"{{:>{col4_len}}}".format(percentage(position, init.schema.endpoints_count)), fg="cyan"  # type:ignore
            )
        if isinstance(result, events.Finished):
            return result.statistic
    return StatsCollector()


def print_header(tests_number: int) -> None:
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
        click.echo(f"Collected endpoints: {tests_number}")
