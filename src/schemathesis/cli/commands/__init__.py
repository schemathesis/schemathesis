from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import click
from tomli import TOMLDecodeError

from schemathesis.cli.commands.data import Data
from schemathesis.cli.commands.run import run as run_command
from schemathesis.cli.commands.run.handlers.output import display_header
from schemathesis.cli.constants import EXTENSIONS_DOCUMENTATION_URL
from schemathesis.cli.core import get_terminal_width
from schemathesis.cli.ext.groups import CommandWithGroupedOptions, GroupedOption
from schemathesis.config import ConfigError, SchemathesisConfig
from schemathesis.core.errors import HookError, format_exception
from schemathesis.core.version import SCHEMATHESIS_VERSION

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT_SETTINGS)  # type: ignore[misc]
@click.option(  # type: ignore[misc]
    "--config-file",
    "config_file",
    help="The path to `schemathesis.toml` file to use for configuration",
    metavar="PATH",
    type=str,
)
@click.pass_context  # type: ignore[misc]
@click.version_option()  # type: ignore[misc]
def schemathesis(ctx: click.Context, config_file: str | None) -> None:
    """Property-based API testing for OpenAPI and GraphQL."""
    try:
        if config_file is not None:
            config = SchemathesisConfig.from_path(config_file)
        else:
            config = SchemathesisConfig.discover()
    except FileNotFoundError:
        display_header(SCHEMATHESIS_VERSION)
        click.secho(
            f"❌  Failed to load configuration file from {config_file}",
            fg="red",
            bold=True,
        )
        click.echo("\nThe configuration file does not exist")
        ctx.exit(1)
    except (TOMLDecodeError, ConfigError) as exc:
        display_header(SCHEMATHESIS_VERSION)
        click.secho(
            f"❌  Failed to load configuration file{f' from {config_file}' if config_file else ''}",
            fg="red",
            bold=True,
        )
        if isinstance(exc, TOMLDecodeError):
            detail = "The configuration file content is not valid TOML"
        else:
            detail = "The loaded configuration is incorrect"
        click.echo(f"\n{detail}\n\n{exc}")
        ctx.exit(1)
    except HookError as exc:
        click.secho("Unable to load Schemathesis extension hooks", fg="red", bold=True)
        formatted_module_name = click.style(f"'{exc.module_path}'", bold=True)
        cause = exc.__cause__
        assert isinstance(cause, Exception)
        if isinstance(cause, ModuleNotFoundError) and cause.name == exc.module_path:
            click.echo(
                f"\nAn attempt to import the module {formatted_module_name} failed because it could not be found."
            )
            click.echo("\nEnsure the module name is correctly spelled and reachable from the current directory.")
        else:
            click.echo(f"\nAn error occurred while importing the module {formatted_module_name}. Traceback:")
            message = format_exception(cause, with_traceback=True, skip_frames=1)
            click.secho(f"\n{message}", fg="red")
        click.echo(f"\nFor more information on how to work with hooks, visit {EXTENSIONS_DOCUMENTATION_URL}")
        ctx.exit(1)
    ctx.obj = Data(config=config)


@dataclass
class Group:
    name: str

    __slots__ = ("name",)

    def add_option(self, *args: Any, **kwargs: Any) -> None:
        run.params.append(GroupedOption(args, group=self.name, **kwargs))


run = schemathesis.command(
    short_help="Execute automated tests based on API specifications",
    cls=CommandWithGroupedOptions,
    context_settings={"terminal_width": get_terminal_width(), **CONTEXT_SETTINGS},
)(run_command)
