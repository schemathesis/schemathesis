import shutil
from contextlib import contextmanager
from typing import Generator, Optional

import click

from .. import runner


@contextmanager
def print_in_section(
    title: str = "", separator: str = "-", start_newline: bool = False, line_length: Optional[int] = None
) -> Generator:
    """Print section in terminal with the given title nicely centered.

    Usage::

        with print_in_section("statistics"):
            print("Number of items:", len(items))
    """
    if start_newline:
        click.echo()

    line_length = line_length or shutil.get_terminal_size((100, 50)).columns

    click.echo((f" {title} " if title else "").center(line_length, separator))
    yield
    click.echo(separator * line_length)


def pretty_print_stats(stats: runner.StatsCollector, hypothesis_out: Optional[str] = None) -> None:
    """Format and print stats collected by :obj:`runner.StatsCollector`."""
    if hypothesis_out:
        with print_in_section("FALSIFYING EXAMPLES", start_newline=True):
            click.echo(click.style(hypothesis_out.strip(), fg="red"))

    if stats.is_empty:
        click.echo(click.style("No checks were performed.", bold=True))
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
