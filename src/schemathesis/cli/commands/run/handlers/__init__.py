import click

from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.commands.run.handlers.cassettes import CassetteWriter
from schemathesis.cli.commands.run.handlers.junitxml import JunitXMLHandler
from schemathesis.cli.commands.run.handlers.output import OutputHandler
from schemathesis.cli.constants import EXTENSIONS_DOCUMENTATION_URL, ISSUE_TRACKER_URL
from schemathesis.core.errors import format_exception

__all__ = [
    "EventHandler",
    "CassetteWriter",
    "JunitXMLHandler",
    "OutputHandler",
    "display_handler_error",
]


def is_built_in_handler(handler: EventHandler) -> bool:
    # Look for exact instances, not subclasses
    return any(type(handler) is class_ for class_ in (CassetteWriter, JunitXMLHandler, OutputHandler))


def display_handler_error(handler: EventHandler, exc: Exception) -> None:
    """Display error that happened within."""
    is_built_in = is_built_in_handler(handler)
    if is_built_in:
        click.secho("Internal Error", fg="red", bold=True)
        click.secho("\nSchemathesis encountered an unexpected issue.")
        message = format_exception(exc, with_traceback=True)
    else:
        click.secho("CLI Handler Error", fg="red", bold=True)
        click.echo(
            f"\nAn error occurred within your custom CLI handler `{click.style(handler.__class__.__name__, bold=True)}`."
        )
        message = format_exception(exc, with_traceback=True, skip_frames=1)
    click.secho(f"\n{message}", fg="red")
    if is_built_in:
        click.echo(
            f"\nWe apologize for the inconvenience. This appears to be an internal issue.\n"
            f"Please consider reporting this error to our issue tracker:\n\n  {ISSUE_TRACKER_URL}."
        )
    else:
        click.echo(
            f"\nFor more information on implementing extensions for Schemathesis CLI, visit {EXTENSIONS_DOCUMENTATION_URL}"
        )
