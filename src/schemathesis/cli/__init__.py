from __future__ import annotations

from schemathesis.cli.commands import Group, run, schemathesis
from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.cli.commands.run.events import LoadingFinished, LoadingStarted
from schemathesis.cli.commands.run.executor import handler
from schemathesis.cli.commands.run.handlers import EventHandler
from schemathesis.cli.ext.groups import GROUPS, OptionGroup

__all__ = [
    "schemathesis",
    "run",
    "EventHandler",
    "ExecutionContext",
    "LoadingStarted",
    "LoadingFinished",
    "add_group",
    "handler",
]


def add_group(name: str, *, index: int | None = None) -> Group:
    """Add a custom options group to `st run`."""
    if index is not None:
        GROUPS[name] = OptionGroup(name=name, order=index)
    else:
        GROUPS[name] = OptionGroup(name=name)
    return Group(name)
