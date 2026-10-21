from __future__ import annotations

import os
import shutil
import sys

import click

from schemathesis.cli.constants import ExitCode
from schemathesis.cli.ext.groups import should_use_color

# Width when stdout is not a terminal, so piped output and snapshots stay stable.
DEFAULT_WIDTH = 80


@click.command(
    name="explain",
    short_help="Explain a failure code",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.argument("code", required=False)
@click.option("--list", "show_list", is_flag=True, help="List every failure code.")
@click.option("--force-color", is_flag=True, help="Force colored output.")
@click.pass_context
def explain(ctx: click.Context, code: str | None, show_list: bool, force_color: bool) -> None:
    """Explain what a failure code means and how to act on it.

    CODE is a failure code (`ST108`, `108`) or a check name.
    """
    from schemathesis.cli.commands.explain import render

    color = True if force_color else should_use_color(ctx)
    width = _output_width()
    if show_list:
        click.echo(render.render_list(width=width, color=color), color=color)
        return
    if code is None:
        raise click.UsageError("Missing a failure code. Run `st explain --list` to see all codes.")
    resolved = render.resolve(code)
    if resolved is None:
        message = f"error: unknown failure code or name `{code.strip()}`"
        suggestion = render.suggest(code)
        if suggestion is not None:
            message += f"\ntip: a similar value exists: {suggestion}"
        click.echo(f"{message}\nRun `st explain --list` to see all codes.", err=True)
        ctx.exit(ExitCode.ERROR)
    else:
        click.echo(render.render_resolved(resolved, width=width, color=color), color=color)


def _output_width() -> int:
    if "COLUMNS" in os.environ or sys.stdout.isatty():
        return shutil.get_terminal_size((DEFAULT_WIDTH, 24)).columns
    return DEFAULT_WIDTH
