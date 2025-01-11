from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import click

from schemathesis.cli import hooks
from schemathesis.cli.commands.run import run as run_command
from schemathesis.cli.core import get_terminal_width
from schemathesis.cli.ext.groups import CommandWithGroupedOptions, GroupedOption

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT_SETTINGS)  # type: ignore[misc]
@click.version_option()  # type: ignore[misc]
def schemathesis() -> None:
    """Property-based API testing for OpenAPI and GraphQL."""
    hooks.load()


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
