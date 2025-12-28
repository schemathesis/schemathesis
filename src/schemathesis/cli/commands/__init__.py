from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

import click

from schemathesis.cli.commands.data import Data
from schemathesis.cli.commands.run import run as run_command
from schemathesis.cli.commands.run.handlers.output import display_header
from schemathesis.cli.constants import EXTENSIONS_DOCUMENTATION_URL
from schemathesis.cli.core import get_terminal_width
from schemathesis.cli.ext.groups import CommandWithGroupedOptions, GroupedOption, StyledGroup, should_use_color
from schemathesis.config import ConfigError, SchemathesisConfig
from schemathesis.core.errors import HookError, format_exception
from schemathesis.core.version import SCHEMATHESIS_VERSION

if sys.version_info < (3, 11):
    from tomli import TOMLDecodeError
else:
    from tomllib import TOMLDecodeError

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}
ROOT_CONTEXT_SETTINGS: dict[str, Any] = {"help_option_names": []}


def _set_no_color(ctx: click.Context, param: click.Parameter, value: bool) -> bool:
    """Eager callback to set color context before help is displayed."""
    if value:
        ctx.color = False
    return value


def _show_help(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """Show help after processing color flags."""
    if value:
        if ctx.color is None:
            ctx.color = should_use_color(ctx)
        click.echo(ctx.get_help(), color=ctx.color)
        ctx.exit()


@click.group(context_settings=ROOT_CONTEXT_SETTINGS, cls=StyledGroup)  # type: ignore[untyped-decorator]
@click.option(  # type: ignore[untyped-decorator]
    "--config-file",
    "config_file",
    help="The path to `schemathesis.toml` file to use for configuration",
    metavar="PATH",
    type=str,
)
@click.option(  # type: ignore[untyped-decorator]
    "--no-color",
    is_flag=True,
    help="Disable colored output",
    is_eager=True,
    callback=_set_no_color,
    expose_value=False,
)
@click.pass_context  # type: ignore[untyped-decorator]
@click.version_option()  # type: ignore[untyped-decorator]
@click.option(  # type: ignore[untyped-decorator]
    "-h",
    "--help",
    is_flag=True,
    help="Show this message and exit",
    is_eager=True,
    callback=_show_help,
    expose_value=False,
)
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
    except PermissionError:
        display_header(SCHEMATHESIS_VERSION)
        click.secho(
            f"❌  Failed to load configuration file from {config_file}",
            fg="red",
            bold=True,
        )
        click.echo("\nPermission denied")
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
