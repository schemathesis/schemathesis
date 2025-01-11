import os
import sys

import click

from schemathesis.cli.constants import EXTENSIONS_DOCUMENTATION_URL
from schemathesis.core.errors import format_exception

HOOKS_MODULE_ENV_VAR = "SCHEMATHESIS_HOOKS"


def load() -> None:
    hooks = os.getenv(HOOKS_MODULE_ENV_VAR)
    if hooks:
        _load(hooks)


def _load(module_name: str) -> None:
    """Load the given hook by importing it."""
    try:
        sys.path.append(os.getcwd())  # fix ModuleNotFoundError module in cwd
        __import__(module_name)
    except Exception as exc:
        click.secho("Unable to load Schemathesis extension hooks", fg="red", bold=True)
        formatted_module_name = click.style(f"'{module_name}'", bold=True)
        if isinstance(exc, ModuleNotFoundError) and exc.name == module_name:
            click.echo(
                f"\nAn attempt to import the module {formatted_module_name} failed because it could not be found."
            )
            click.echo("\nEnsure the module name is correctly spelled and reachable from the current directory.")
        else:
            click.echo(f"\nAn error occurred while importing the module {formatted_module_name}. Traceback:")
            message = format_exception(exc, with_traceback=True, skip_frames=1)
            click.secho(f"\n{message}", fg="red")
        click.echo(f"\nFor more information on how to work with hooks, visit {EXTENSIONS_DOCUMENTATION_URL}")
        raise click.exceptions.Exit(1) from None
